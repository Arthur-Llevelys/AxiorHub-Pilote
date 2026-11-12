"""5.6.95 (lot routage, G01 à G05) : routage mesuré. Données fictives ; aucun modèle ni fournisseur réel n'est appelé (ChatGPT et Mistral
répondent depuis des faux services ; les mesures d'usage sont des lignes fictives de ``decisions_5682`` ou des agrégats posés par le test).

Les garde-fous de politique (G02) sont prouvés par mutation : sur une COPIE en mémoire de ``routage5682`` dont le garde-fou est neutralisé,
les mêmes mesures font choisir le fournisseur interdit ; sur le code réel, jamais. L'arbre n'est jamais modifié."""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
import types
import unittest
from unittest.mock import patch
import uuid

import test_v5682 as r82
from agent import ai_gateway, budget_ledger5689, catalogue5682, chatgpt5662, data_policy5689, politique5682, routage5682, routage5695, routage_web5695
from agent.arret5643 import CODE_HORAIRE, pause_all
from agent.common import Stop

MOIS = ('gpt-6-luna', 'gpt-6.1-terra', 'gpt-6.1-sol', 'gpt-6-astra')
MISTRAL = ('mistral-small-latest', 'mistral-large-4')


def _iso(moment):
    return moment.isoformat()


def maintenant():
    return datetime.now(timezone.utc)


def vider_caches():
    for cache in (routage5695._derniers, routage5695._cache_mesures, routage5695._cache_reglage, routage5695._cache_evaluation):
        cache.clear()


class Fond(r82.Strict):
    """Bureau fictif ; caches du module vidés ; aides pour poser des décisions, des réservations et des agrégats fictifs."""

    def setUp(self):
        super().setUp()
        vider_caches()
        self.addCleanup(vider_caches)
        self.state = self.desk.c['state_dir']

    def db(self):
        db = routage5682._connect(self.state)
        self.addCleanup(db.close)
        return db

    def decision(self, debut, fournisseur, modele, type_tache, etat='terminee', code='', duree_ms=1000, mission='', etape='triage', escalade_de='',
                 mode='auto', raisons=(), unite='aucune', mesure_choix='', mesure_mode='', raison_courte='Travail courant.', dossier_h=''):
        uid = uuid.uuid4().hex
        db = routage5682._connect(self.state)
        try:
            db.execute('INSERT INTO decisions_5682(uid,at,mission,etape,proprietaire,dossier_h,fonction,type_tache,niveau,fournisseur,modele,effort,raisons,'
                       'raison_courte,mode,politique_version,estimation,unite,provisoire,escalade_de,automatique,etat,code,fini,mesure_mode,mesure_choix) '
                       'VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                       (uid, _iso(debut), mission, etape, 'avocat@example.test', dossier_h, 'mail_triage', type_tache, 'standard', fournisseur, modele, '',
                        json.dumps(list(raisons)), raison_courte, mode, 1, None, unite, 0, escalade_de, 0, etat, code,
                        _iso(debut + timedelta(milliseconds=duree_ms)) if etat != 'decidee' else '', mesure_mode, mesure_choix))
            db.commit()
        finally:
            db.close()
        return uid

    def reservation(self, uid, micro, devise='USD', etat='settled'):
        db = budget_ledger5689.connect(self.state)
        try:
            db.execute('INSERT INTO budget_reservations5689(cabinet_id,reservation_id,mission_id,call_id,idempotency_key,provider,model,purpose,currency,'
                       'ceiling_micro,retained_micro,pricing_version,created_at,state) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                       ('cabinet', uuid.uuid4().hex, '', uid, uid, 'x', 'x', 'mail_drafting', devise, micro, micro, '', _iso(maintenant()), etat))
            db.commit()
        finally:
            db.close()

    def semer(self, fournisseur, modeles, type_tache, appels, reussis, mediane_ms=2000):
        """Agrégats fictifs du jour (le filigrane est placé après les décisions existantes : rien n'est compté deux fois)."""
        db = routage5695._connect(self.state)
        try:
            jour = maintenant().date().isoformat()
            durees = [0] * (len(routage5695.BORNES_MS) + 1)
            durees[routage5695._case(mediane_ms)] = appels
            for modele in modeles:
                db.execute('INSERT OR REPLACE INTO routage5695_agregats(jour,fournisseur,modele,type_tache,appels,terminees,durees,locaux) VALUES(?,?,?,?,?,?,?,?)',
                           (jour, fournisseur, modele, type_tache, appels, reussis, json.dumps(durees), appels if fournisseur == 'ollama' else 0))
            if routage5695._meta(db, 'filigrane') is None:
                fin = db.execute('SELECT COALESCE(MAX(id),0) FROM decisions_5682').fetchone()[0]
                db.execute('INSERT OR REPLACE INTO routage5695_meta VALUES(?,?)', ('filigrane', str(fin)))
            db.commit()
        finally:
            db.close()
        routage5695._cache_mesures.clear()

    def actif(self):
        self.desk.setting(routage5695.REGLAGE, {'valeur': 'actif', 'revision': 1})
        routage5695._cache_reglage.clear()

    def auto(self, fournisseurs=('chatgpt', 'mistral')):
        self.fake = r82.Fake()
        http = patch.object(chatgpt5662, 'http', self.fake)
        http.start()
        self.addCleanup(http.stop)
        r82.connect(self.desk)
        catalogue5682.refresh(self.desk, 'chatgpt')
        chatgpt5662.save_provider(self.desk, 'avocat', 'gpt-6-luna', True, True)
        ai_gateway.save_provider(self.desk, 'mistral', 'mistral', 'https://api.mistral.ai/v1', 'mistral-large-4', 'cle-fictive', True, True)
        with patch('agent.modeles5681._fetch', return_value={'data': [{'id': 'mistral-small-latest'}, {'id': 'mistral-large-4'}]}):
            politique5682.save(self.desk, {'mode': 'auto', 'consentement': True, 'fournisseurs': list(fournisseurs)}, 'avocat')

    def local(self):
        return str((self.desk.c.get('ollama') or {}).get('model') or '')

    def decider(self, module=routage5682, payload=None):
        return module.decide(self.desk.c, 'mail_triage', 'triage', payload or {'text': 'x'})


def mutant(*remplacements):
    """Copie en mémoire de ``routage5682`` avec un garde-fou neutralisé. Chaque passage doit exister une fois : la mutation porte bien sur lui."""
    source = Path(routage5682.__file__).read_text(encoding='utf-8')
    for avant, apres in remplacements:
        assert source.count(avant) == 1, avant
        source = source.replace(avant, apres)
    module = types.ModuleType('agent.routage5682_mutant')
    module.__package__ = 'agent'
    module.__file__ = routage5682.__file__
    exec(compile(source, 'routage5682_mutant', 'exec'), module.__dict__)
    return module


# ------------------------------------------------------------------------------------------------ G01
class Mesures(Fond):
    def test_G01_agregats_par_fournisseur_modele_type(self):
        t0 = maintenant() - timedelta(days=2)
        for i in range(10):
            self.decision(t0 + timedelta(minutes=i), 'ollama', 'modele-local', 'classement', duree_ms=1000)
        luna = []
        for i in range(24):
            luna.append(self.decision(t0 + timedelta(hours=1, minutes=i), 'chatgpt', 'gpt-6-luna', 'redaction_mail', duree_ms=18000, unite='credits', etape='compose'))
        for i in range(3):
            fautif = self.decision(t0 + timedelta(hours=2, minutes=i), 'chatgpt', 'gpt-6-luna', 'redaction_mail', 'erreur', 'schema_ia_invalide', 5000, unite='credits')
            self.decision(t0 + timedelta(hours=2, minutes=i, seconds=10), 'chatgpt', 'gpt-6.1-terra', 'redaction_mail', duree_ms=20000, escalade_de=fautif, unite='credits')
        for i in range(2):
            self.decision(t0 + timedelta(hours=3, minutes=i), 'chatgpt', 'gpt-6-luna', 'redaction_mail', 'erreur', 'chatgpt_indisponible', 3000, unite='credits')
        self.decision(t0 + timedelta(hours=4), 'chatgpt', 'gpt-6-luna', 'redaction_mail', 'erreur', 'ia_en_pause', 0, unite='credits')
        # brouillon puis correction demandée après le contrôle de rédaction (même travail, même étape)
        self.decision(t0 + timedelta(hours=5), 'chatgpt', 'gpt-6-luna', 'redaction_mail', mission='77', etape='manual_draft', duree_ms=18000, unite='credits')
        self.decision(t0 + timedelta(hours=5, minutes=1), 'chatgpt', 'gpt-6-luna', 'redaction_mail', mission='77', etape='manual_draft', duree_ms=9000,
                      raisons=['base_redaction_mail', 'correction_redaction'], unite='credits')
        for uid in luna[:4]:
            self.reservation(uid, 2_500_000, 'CRD')
        m = self.decision(t0 + timedelta(hours=6), 'mistral', 'mistral-large-4', 'analyse_juridique', duree_ms=30000, unite='usd')
        self.reservation(m, 12_000)
        self.assertEqual(routage5695.rafraichir(self.state, force=True), 10 + 24 + 6 + 2 + 1 + 2 + 1)
        mesures = routage5695.mesures(self.state)
        local = mesures[('ollama', 'modele-local', 'classement')]
        self.assertEqual((local['appels'], local['reussite'], local['local']), (10, 1.0, True))
        self.assertTrue(800 <= local['mediane_ms'] <= 1300)
        l = mesures[('chatgpt', 'gpt-6-luna', 'redaction_mail')]
        # 24 + 3 invalides + 2 indisponibles + brouillon + correction ; la pause ne dit rien du modèle
        self.assertEqual((l['appels'], l['arrets'], l['invalides'], l['escalades'], l['relais']), (31, 1, 3, 3, 2))
        self.assertEqual((l['brouillons'], l['corrections']), (25, 1))       # la correction elle-même n'est pas un brouillon
        self.assertEqual(l['valides'], 26 - 1)                                # terminées moins le brouillon qu'il a fallu corriger
        self.assertAlmostEqual(l['reussite'], 25 / 31)
        self.assertAlmostEqual(l['taux_controle'], 1 / 25)
        self.assertAlmostEqual(l['cout_credits'], 2.5)
        self.assertTrue(12000 <= l['mediane_ms'] <= 22000 and l['p95_ms'] >= l['mediane_ms'])
        self.assertEqual(mesures[('chatgpt', 'gpt-6.1-terra', 'redaction_mail')]['appels'], 3)
        self.assertAlmostEqual(mesures[('mistral', 'mistral-large-4', 'analyse_juridique')]['cout_usd'], 0.012)
        self.assertTrue(l['suffisant'])
        self.assertFalse(local['suffisant'])                                  # 10 appels : sous le seuil de 20

    def test_G01_correction_de_brouillon_marquee_par_le_chemin_reel(self):
        """La demande de correction du lot rédaction (``correction_demandee`` dans la charge) passe par ``Model.ask`` : la décision porte un code."""
        self.strict()
        self.model().ask('triage', {'subject': 'Objet fictif', 'correction_demandee': {'ecarts': [{'ecart': 'marqueurs_trop_nombreux'}]}})
        self.model().ask('triage', {'subject': 'Objet fictif'})
        raisons = [json.loads(r[0]) for r in self.db().execute('SELECT raisons FROM decisions_5682 ORDER BY id')]
        self.assertIn('correction_redaction', raisons[0])
        self.assertNotIn('correction_redaction', raisons[1])
        self.assertFalse(any('marqueurs_trop_nombreux' in x for r in raisons for x in r))      # aucun détail de la correction n'est gardé

    def test_G01_incremental_borne_et_decision_ouverte_attendue(self):
        t0 = maintenant() - timedelta(hours=3)
        for i in range(4):
            self.decision(t0 + timedelta(minutes=i), 'ollama', 'modele-local', 'classement')
        self.assertEqual(routage5695.rafraichir(self.state), 4)
        appels = []
        with patch.object(routage5695, '_connect', side_effect=lambda *a, **k: appels.append(1)):
            self.assertIsNone(routage5695.rafraichir(self.state))           # moins de 15 min : aucune lecture, aucune connexion
        self.assertEqual(appels, [])
        ouverte = self.decision(maintenant() - timedelta(minutes=1), 'ollama', 'modele-local', 'classement', 'decidee')
        self.decision(maintenant(), 'ollama', 'modele-local', 'classement')
        plus_tard = maintenant() + timedelta(minutes=16)
        with patch.object(routage5695.time, 'monotonic', return_value=routage5695.time.monotonic() + 1000):
            self.assertEqual(routage5695.rafraichir(self.state, maintenant=plus_tard), 0)   # 15 min plus tard : l'appel en cours bloque le filigrane
        db = self.db()
        db.execute("UPDATE decisions_5682 SET etat='terminee',fini=? WHERE uid=?", (_iso(maintenant()), ouverte))
        db.commit()
        self.assertEqual(routage5695.rafraichir(self.state, force=True), 2)   # seulement les nouvelles décisions
        self.assertEqual(routage5695.mesures(self.state)[('ollama', 'modele-local', 'classement')]['appels'], 6)
        # borne : au plus LOT décisions par mise à jour ; un retard se rattrape à la suivante sans attendre 15 min
        for i in range(7):
            self.decision(maintenant() + timedelta(seconds=i), 'ollama', 'modele-local', 'classement')
        with patch.object(routage5695, 'LOT', 5):
            self.assertEqual(routage5695.rafraichir(self.state, force=True), 5)
            self.assertEqual(routage5695.rafraichir(self.state), 2)
        routage5695._cache_mesures.clear()
        self.assertEqual(routage5695.mesures(self.state)[('ollama', 'modele-local', 'classement')]['appels'], 13)

    def test_G01_decision_abandonnee_sans_issue_et_retention(self):
        self.decision(maintenant() - timedelta(hours=2), 'ollama', 'modele-local', 'classement', 'decidee')
        db = routage5695._connect(self.state)
        db.execute("INSERT INTO routage5695_agregats(jour,fournisseur,modele,type_tache,appels) VALUES(?,?,?,?,?)",
                   ((maintenant() - timedelta(days=routage5695.RETENTION + 2)).date().isoformat(), 'ollama', 'ancien', 'classement', 9))
        db.commit()
        db.close()
        routage5695.rafraichir(self.state, force=True)
        m = routage5695.mesures(self.state, 90)[('ollama', 'modele-local', 'classement')]
        self.assertEqual((m['appels'], m['sans_issue'], m['valides']), (1, 1, 0))
        db = self.db()
        self.assertEqual(db.execute("SELECT COUNT(*) FROM routage5695_agregats WHERE modele='ancien'").fetchone()[0], 0)

    def test_G01_aucun_texte_ni_dossier_dans_les_agregats(self):
        self.decision(maintenant() - timedelta(hours=1), 'chatgpt', 'gpt-6-luna', 'redaction_mail', raison_courte='TEXTE-CONFIDENTIEL DURAND contre MARTIN',
                      dossier_h='a1b2c3d4e5f60718', mission='12', unite='credits')
        routage5695.rafraichir(self.state, force=True)
        db = self.db()
        contenu = json.dumps([[tuple(r) for r in db.execute('SELECT * FROM %s' % t)] for t in ('routage5695_agregats', 'routage5695_choix', 'routage5695_meta')])
        for interdit in ('TEXTE-CONFIDENTIEL', 'DURAND', 'MARTIN', 'a1b2c3d4e5f60718', 'avocat@example.test', 'mail_triage'):
            self.assertNotIn(interdit, contenu)
        colonnes = {r[1] for t in ('routage5695_agregats', 'routage5695_choix') for r in db.execute('PRAGMA table_info(%s)' % t)}
        self.assertFalse(colonnes & {'raison_courte', 'dossier_h', 'proprietaire', 'raisons', 'mission', 'fonction'})

    def test_G01_base_illisible_jamais_un_faux_zero(self):
        with patch.object(routage5695, '_connect', side_effect=sqlite3.OperationalError('base occupée')):
            self.assertIsNone(routage5695.mesures(self.state))
            self.assertIsNone(routage5695.rafraichir(self.state, force=True))
            html = routage5695.section_html(self.desk, '', {})
        self.assertIn('Mesures indisponibles', html)
        self.assertNotIn('<td>0</td>', html)

    def test_G01_aucun_appel_au_modele_pour_mesurer(self):
        self.decision(maintenant() - timedelta(hours=1), 'ollama', 'modele-local', 'classement')
        interdit = Stop('appel_modele_interdit_dans_le_test')
        with patch('agent.model.Model.complete', side_effect=interdit), patch('agent.model.Model.ask', side_effect=interdit), \
                patch('agent.model.Model._call_child', side_effect=interdit), patch('urllib.request.urlopen', side_effect=interdit):
            routage5695.rafraichir(self.state, force=True)
            routage5695.mesures(self.state)
            routage5695.section_html(self.desk, '', {})
            routage5695.export_csv(self.desk, 30)


# ------------------------------------------------------------------------------------------------ G02
class Qualite(Fond):
    def test_G02_ponderation_banc_et_usage(self):
        usage = {'appels': 20, 'reussite': 0.9, 'suffisant': True}
        self.assertEqual(routage5695.score({'score': 70}, usage), (80.0, 'banc+usage'))           # 0,5 × 90 + 0,5 × 70
        self.assertEqual(routage5695.score({'score': 70}, {**usage, 'appels': 100}), (86.0, 'banc+usage'))   # 0,8 × 90 + 0,2 × 70
        self.assertEqual(routage5695.score(None, usage), (90.0, 'usage'))
        self.assertEqual(routage5695.score({'score': 70}, {'appels': 19, 'reussite': 1.0, 'suffisant': False}), (70.0, 'banc'))
        self.assertEqual(routage5695.score(None, None), (None, ''))
        self.assertAlmostEqual(routage5695.poids_usage(60), 0.65)

    def test_G02_sans_mesure_suffisante_comportement_inchange_et_provisoire(self):
        self.auto()
        conseil = self.decider()
        self.semer('mistral', MISTRAL, 'classement', 19, 19)       # sous le seuil d'échantillon
        self.actif()
        d = self.decider()
        self.assertEqual((d['fournisseur'], d['modele']), (conseil['fournisseur'], conseil['modele']))
        self.assertTrue(d['routage_mesure']['provisoire'])
        self.assertFalse(d['routage_mesure']['suffisant'])
        self.assertEqual(d['raison_courte'], conseil['raison_courte'])

    def _politique_chatgpt_seule(self):
        self.auto()
        politique5682.save(self.desk, {'mode': 'chatgpt', 'consentement': True}, 'avocat')
        self.semer('mistral', MISTRAL, 'classement', 500, 500, 900)     # mesures parfaites pour un fournisseur interdit
        self.semer('chatgpt', MOIS, 'classement', 100, 30, 30000)
        self.actif()

    def test_G02_chatgpt_uniquement_jamais_un_autre_fournisseur_mutation(self):
        self._politique_chatgpt_seule()
        d = self.decider()
        self.assertEqual(d['type_fournisseur'], 'chatgpt')
        self.assertIn('n’atteint le seuil', d['routage_mesure']['raison'])
        with patch.object(politique5682, 'candidates', lambda config, rule: ['chatgpt', 'mistral']):    # mutation : filtre du mode retiré
            self.assertEqual(self.decider()['fournisseur'], 'mistral')

    def test_G02_local_uniquement_mutation(self):
        self.auto(('ollama', 'chatgpt', 'mistral'))
        politique5682.save(self.desk, {'mode': 'local', 'consentement': True, 'fournisseurs': ['ollama', 'chatgpt', 'mistral']}, 'avocat')
        self.assertIsNone(politique5682.routed(self.desk.c, 'mail_triage', 'fast'))     # le modèle ne passe même pas par le routeur
        self.semer('mistral', MISTRAL, 'classement', 500, 500, 900)
        self.semer('chatgpt', MOIS, 'classement', 500, 495, 900)
        self.semer('ollama', [self.local()], 'classement', 100, 30, 60000)
        self.actif()
        self.assertEqual(self.decider()['fournisseur'], 'ollama')
        # ancienne liste restée enregistrée ET connexions externes réactivées : deux garde-fous indépendants tiennent « local uniquement »
        # (celui du routeur, 5.6.95, et la destination admise « mode_local_strict » de data_policy5689) ; chacun suffit seul
        self.auto(('ollama', 'chatgpt', 'mistral'))
        self.desk.setting(politique5682.KEY, {**self.desk.settings(politique5682.KEY, {}), 'mode': 'local'})
        self.assertEqual(politique5682.read_fresh(self.state)['mode'], 'local')
        self.assertEqual(self.decider()['fournisseur'], 'ollama')
        routeur = ("        if kind != 'ollama' and rule['mode'] == 'local':", "        if False:")
        destination = ("        if not verdict['allowed']:", "        if False:")
        self.assertEqual(self.decider(mutant(routeur))['fournisseur'], 'ollama')
        self.assertEqual(self.decider(mutant(destination))['fournisseur'], 'ollama')
        self.assertNotEqual(self.decider(mutant(routeur, destination))['fournisseur'], 'ollama')

    def test_G02_destination_admise_mutation(self):
        self.auto()
        self.semer('mistral', MISTRAL, 'classement', 500, 500, 900)
        self.semer('chatgpt', MOIS, 'classement', 100, 30, 30000)
        self.actif()
        vrai = data_policy5689.destination_admise

        def refus_mistral(config, provider_id, provider, purpose, matters):
            if provider_id == 'mistral':
                return {'allowed': False, 'denial_reasons': ['destination_non_admise:mistral']}
            return vrai(config, provider_id, provider, purpose, matters)
        with patch.object(data_policy5689, 'destination_admise', refus_mistral):
            self.assertEqual(self.decider()['fournisseur'], 'chatgpt')
            copie = mutant(("        if not verdict['allowed']:", "        if False:"))
            self.assertEqual(self.decider(copie)['fournisseur'], 'mistral')

    def test_G02_dossier_reserve_mutation(self):
        self.auto(('ollama', 'chatgpt', 'mistral'))
        self.desk.c['hybrid_routing'] = {'mode': 'manual', 'excluded_matters': ['DOS-001']}
        self.semer('mistral', MISTRAL, 'classement', 500, 500, 900)
        self.semer('chatgpt', MOIS, 'classement', 500, 495, 900)
        self.semer('ollama', [self.local()], 'classement', 100, 30, 60000)
        self.actif()
        with patch.object(routage5682, '_matters', return_value={'DOS-001'}):
            self.assertEqual(self.decider(payload={'matter': 'DOS-001'})['fournisseur'], 'ollama')
        copie = mutant(("        if kind != 'ollama' and reserved:", "        if False:"))
        with patch.object(copie, '_matters', return_value={'DOS-001'}), \
                patch.object(data_policy5689, 'destination_admise', lambda *a: {'allowed': True, 'denial_reasons': []}):
            self.assertNotEqual(self.decider(copie, {'matter': 'DOS-001'})['fournisseur'], 'ollama')

    def test_G02_pause_et_horaires_avant_tout_classement_mutation(self):
        self.auto()
        self.semer('mistral', MISTRAL, 'classement', 500, 500, 900)
        self.actif()
        copie = mutant(('    code = reason_to_stop(state_dir)\n    if code:\n        raise Stop(code)', '    code = reason_to_stop(state_dir)'))
        pause_all(self.desk, True)
        avant = self.db().execute('SELECT COUNT(*) FROM decisions_5682').fetchone()[0]
        with self.assertRaises(Stop) as ctx:
            self.decider()
        self.assertEqual(str(ctx.exception), 'ia_en_pause')
        self.assertEqual(self.db().execute('SELECT COUNT(*) FROM decisions_5682').fetchone()[0], avant)    # aucune décision, aucun appel
        self.assertEqual(self.decider(copie)['fournisseur'], 'mistral')
        pause_all(self.desk, False)
        with patch('agent.arret5643._read_all', return_value=(False, set(), CODE_HORAIRE)):
            with self.assertRaises(Stop) as ctx:
                self.decider()
            self.assertEqual(str(ctx.exception), CODE_HORAIRE)
            self.assertEqual(self.decider(copie)['fournisseur'], 'mistral')

    def test_G02_budget_du_fournisseur_et_de_la_mission_mutation(self):
        self.auto()
        self.semer('mistral', MISTRAL, 'classement', 500, 500, 900)
        self.semer('chatgpt', MOIS, 'classement', 100, 30, 30000)
        self.actif()
        vrai = budget_ledger5689.admissible
        with patch.object(budget_ledger5689, 'admissible', lambda state_dir, pid, cfg, montant: pid != 'mistral' and vrai(state_dir, pid, cfg, montant)):
            self.assertEqual(self.decider()['fournisseur'], 'chatgpt')
            copie = mutant(("    if kind not in ('ollama', 'chatgpt') and not budget_ledger5689.admissible(", "    if False and not budget_ledger5689.admissible("))
            self.assertEqual(self.decider(copie)['fournisseur'], 'mistral')
        # budget de la mission : le choix mesuré (API) est réservé AVANT l'appel ; plafond atteint, rien ne part, aucun autre fournisseur n'est essayé
        politique5682.save(self.desk, {'mode': 'auto', 'consentement': True, 'fournisseurs': ['chatgpt', 'mistral'], 'plafond_mission_usd': '0.000001'}, 'avocat')
        with patch.object(routage5682, '_mission', return_value='mission-fictive'):
            with self.assertRaises(Stop) as ctx:
                self.decider()
            self.assertEqual(str(ctx.exception), 'budget_mission_atteint')
        copie = mutant(('    reservation = conso5682.reserve(state_dir, mission, estimate, prices[3], limit, uid, provider_id, row[\'slug\'], purpose)',
                        '    reservation = 0'))
        with patch.object(copie, '_mission', return_value='mission-fictive'):
            self.assertEqual(self.decider(copie)['fournisseur'], 'mistral')

    def test_G02_choix_toujours_dans_la_liste_filtree(self):
        self.auto()
        found = []
        vrai = routage5695._evaluer

        def espion(config, candidats, *a):
            found.append([c[0] for c in candidats])
            return vrai(config, candidats, *a)
        self.semer('mistral', MISTRAL, 'classement', 500, 500, 900)
        self.semer('chatgpt', MOIS, 'classement', 100, 99, 900)
        self.actif()
        politique5682.save(self.desk, {'mode': 'chatgpt', 'consentement': True}, 'avocat')
        with patch.object(routage5695, '_evaluer', espion):
            self.decider()
        self.assertEqual({p for liste in found for p in liste}, {'chatgpt'})
        self.assertEqual(len(found), 1)
        # un choix hors de la liste serait refusé par le garde-fou d'``evaluer`` lui-même
        item = ('chatgpt', {}, {'slug': 'gpt-6-luna'}, 0, (None, None, None, 'credits'), None)
        intrus = ('mistral', {}, {'slug': 'mistral-small-latest'}, 0, (1.0, None, 1.0, 'usd'), None)
        with patch.object(routage5695, 'min', create=True, side_effect=lambda *a, **k: {'item': intrus, 'score': 99, 'usage': None, 'banc': None, 'source': 'usage'}):
            self.assertIsNone(routage5695.evaluer(self.desk.c, [item], 'classement', 60, lambda i: (0, 1, 0.0, 0.0, 0), item))


# ------------------------------------------------------------------------------------------------ G03
class Reglage(Fond):
    def _mesures(self):
        self.auto()
        self.semer('mistral', MISTRAL, 'classement', 200, 196, 18000)
        self.semer('chatgpt', MOIS, 'classement', 50, 20, 30000)

    def test_G03_conseil_par_defaut_decision_inchangee_choix_mesure_enregistre(self):
        self._mesures()
        self.assertEqual(routage5695.mode(self.state), 'conseil')
        d = self.decider()
        self.assertEqual(d['type_fournisseur'], 'chatgpt')                     # comme aujourd'hui : l'abonnement avant l'API à niveau égal
        m = d['routage_mesure']
        self.assertEqual((m['mode'], m['fournisseur'], m['identique']), ('conseil', 'mistral', False))
        self.assertEqual(m['raison'], 'Mistral : 98 % de réussite sur 200 appels (classement), 18 s médian ; ChatGPT : 40 %.')
        row = self.db().execute('SELECT fournisseur,raison_courte,mesure_mode,mesure_choix,mesure_raison FROM decisions_5682 WHERE uid=?', (d['uid'],)).fetchone()
        self.assertEqual((row['fournisseur'], row['mesure_mode']), ('chatgpt', 'conseil'))
        self.assertTrue(row['mesure_choix'].startswith('mistral|'))
        self.assertNotIn('réussite', row['raison_courte'])
        self.assertEqual(row['mesure_raison'], m['raison'])

    def test_G03_actif_applique_avec_raison_lisible(self):
        self._mesures()
        self.actif()
        d = self.decider()
        self.assertEqual(d['fournisseur'], 'mistral')
        self.assertFalse(d['provisoire'])
        self.assertTrue(d['raison_courte'].startswith('Mistral : 98 % de réussite sur 200 appels (classement), 18 s médian ; ChatGPT : 40 %.'))
        row = self.db().execute('SELECT fournisseur,raison_courte,mesure_mode FROM decisions_5682 WHERE uid=?', (d['uid'],)).fetchone()
        self.assertEqual((row['fournisseur'], row['raison_courte'], row['mesure_mode']), ('mistral', d['raison_courte'], 'actif'))

    def test_G03_reglage_administrateur_revision_et_repetition(self):
        r = routage5695.reglage(self.desk)
        self.assertEqual(r, {'valeur': 'conseil', 'revision': 0})
        out = routage5695.enregistrer_mode(self.desk, 'actif', 0, 'admin@example.test')
        self.assertEqual((out['valeur'], out['revision']), ('actif', 1))
        self.assertEqual(routage5695.mode(self.state), 'actif')
        self.assertEqual(routage5695.enregistrer_mode(self.desk, 'actif', 0, 'admin@example.test')['revision'], 1)   # même demande répétée : sans effet
        with self.assertRaises(Stop) as ctx:
            routage5695.enregistrer_mode(self.desk, 'conseil', 0, 'admin@example.test')                              # révision périmée
        self.assertEqual(str(ctx.exception), 'revision_perimee')
        with self.assertRaises(Stop):
            routage5695.enregistrer_mode(self.desk, 'automatique', 1, 'admin@example.test')
        self.assertTrue(routage_web5695.allowed('administrateur', 'routage/mode'))
        self.assertFalse(routage_web5695.allowed('avocat', 'routage/mode'))
        from agent.standalone_auth import ADMIN_API, allowed as allowed_auth
        self.assertTrue('/api440/m5689/routage/mode'[len('/api440/'):].startswith(ADMIN_API))
        self.assertFalse(allowed_auth('avocat', 'POST', '/api440/m5689/routage/mode'))
        env = {'HTTP_X_AXIORHUB_ROLE': 'avocat', 'HTTP_X_AXIORHUB_USER': 'avocat@example.test'}
        reponse = routage_web5695.api(env, self.desk, {}, '', 'm5689/routage/export', {'periode': '30'}, 'GET')
        self.assertTrue(reponse['status'].startswith('403'))

    def test_G03_route_conflit_409(self):
        import io
        auth = {'origin': 'https://axiorhub.example.test', 'csrf': 'jeton-csrf-fictif'}

        def poste(data, role='administrateur'):
            corps = json.dumps(data).encode()
            env = {'HTTP_ORIGIN': auth['origin'], 'HTTP_X_CSRF_TOKEN': auth['csrf'], 'CONTENT_TYPE': 'application/json', 'CONTENT_LENGTH': str(len(corps)),
                   'wsgi.input': io.BytesIO(corps), 'HTTP_X_AXIORHUB_ROLE': role, 'HTTP_X_AXIORHUB_USER': 'admin@example.test'}
            return routage_web5695.api(env, self.desk, auth, '', 'm5689/routage/mode', {}, 'POST')
        self.assertTrue(poste({'valeur': 'actif', 'revision': 0})['status'].startswith('200'))
        self.assertTrue(poste({'valeur': 'conseil', 'revision': 0})['status'].startswith('409'))
        self.assertTrue(poste({'valeur': 'conseil', 'revision': 1}, 'avocat')['status'].startswith('403'))
        self.assertEqual(routage5695.reglage(self.desk)['valeur'], 'actif')


# ------------------------------------------------------------------------------------------------ G04
class Page(Fond):
    def test_G04_page_tableau_periode_export_sans_nom_de_dossier(self):
        self.auto()
        t0 = maintenant() - timedelta(days=3)
        for i in range(25):
            self.decision(t0 + timedelta(minutes=i), 'chatgpt', 'gpt-6-luna', 'redaction_mail', unite='credits', duree_ms=18000, dossier_h='0f0f0f0f0f0f0f0f',
                          mesure_mode='conseil', mesure_choix='mistral|mistral-large-4' if i % 5 == 0 else 'chatgpt|gpt-6-luna')
        self.decision(maintenant() - timedelta(days=20), 'mistral', 'mistral-large-4', 'redaction_mail', unite='usd')
        routage5695.rafraichir(self.state, force=True)
        html = routage5695.section_html(self.desk, '/axh', {'QUERY_STRING': 'rubrique=ia&periode=7'})
        self.assertIn('id="routage5695"', html)
        self.assertIn('ChatGPT · gpt-6-luna', html)
        self.assertIn('Mistral · mistral-large-4</th><td>Oui</td><td>0 (échantillon insuffisant)</td>', html)   # permis, appel hors des 7 jours
        self.assertIn('écart : 20 %', html)
        self.assertIn('<option value="7" selected>', html)
        self.assertIn('/axh/api440/m5689/routage/export?periode=7', html)
        self.assertNotIn('DOS-001', html)
        self.assertNotIn('0f0f0f0f0f0f0f0f', html)
        self.assertNotIn('<script', html)
        self.assertNotIn('style=', html)
        large = routage5695.section_html(self.desk, '', {'QUERY_STRING': 'periode=90'})
        self.assertIn('Mistral · mistral-large-4</th><td>Oui</td><td>1 (échantillon insuffisant)</td>', large)
        avocat = routage5695.section_html(self.desk, '', {'HTTP_X_AXIORHUB_ROLE': 'avocat'})
        self.assertIn('disabled', avocat)
        self.assertNotIn('type="submit">Enregistrer', avocat)
        nom, texte = routage5695.export_csv(self.desk, 90)
        self.assertEqual(nom, 'routage-mesure-90j.csv')
        lignes = texte.lstrip('﻿').splitlines()
        self.assertTrue(lignes[0].startswith('periode_jours;type_tache;'))
        luna = next(l for l in lignes if ';gpt-6-luna;' in l).split(';')
        self.assertEqual(luna[6], '25')
        self.assertNotIn('0f0f0f0f0f0f0f0f', texte)
        reponse = routage_web5695.api({}, self.desk, {}, '', 'm5689/routage/export', {'periode': '90'}, 'GET')
        self.assertEqual(reponse['kind'], 'text/csv; charset=utf-8')

    def test_G04_textes_dans_les_quatre_langues(self):
        from agent import i18n5631
        cles = {k for k in i18n5631.catalog('fr') if k.startswith('routage5695.')}
        self.assertGreaterEqual(len(cles), 40)
        for langue in ('en', 'de', 'es'):
            self.assertEqual({k for k in i18n5631.catalog(langue) if k.startswith('routage5695.')}, cles, langue)
        i18n5631.set_lang('de')
        self.addCleanup(i18n5631.set_lang, 'fr')
        self.assertIn('Gemessene Modellwahl', routage5695.section_html(self.desk, '', {}))

    def test_G04_section_dans_parametres_ia(self):
        from agent import parametres5621
        from agent.parametres5621 import Capture
        html = parametres5621._ia(self.desk, {'csrf': 'x'}, '', {'QUERY_STRING': 'rubrique=ia'}, Capture())
        self.assertIn('id="routage5695"', html)


# ------------------------------------------------------------------------------------------------ G05
class Evaluation(Fond):
    def rapport(self, contenu):
        dossier = Path(self.state, 'evaluation-redaction')
        dossier.mkdir(exist_ok=True)
        (dossier / 'rapport.json').write_text(json.dumps(contenu) if not isinstance(contenu, str) else contenu, encoding='utf-8')
        routage5695._cache_evaluation.clear()

    def test_G05_rapport_d_evaluation_source_du_banc_pour_les_brouillons(self):
        self.assertIsNone(routage5695.evaluation(self.state))
        self.rapport({'date': '2026-11-07T08:00:00+00:00', 'modele': 'modele-local', 'versions': {
            '5.6.94': {'synthese': {'cas': 24, 'regle_respectee': 0.2}}, '5.6.97': {'synthese': {'cas': 24, 'regle_respectee': 0.875, 'duree_redaction_mediane_s': 41.5}}}})
        ev = routage5695.evaluation(self.state)
        self.assertEqual((ev['fournisseur'], ev['modele'], ev['score'], ev['samples'], ev['delai'], ev['etiquette']), ('ollama', 'modele-local', 87.5, 24, 41500, '5.6.97'))
        local = ('ollama', {'type': 'ollama'}, {'slug': 'modele-local'}, 0, (0.0, None, 0.0, 'aucune'), None)
        api = ('mistral', {'type': 'mistral'}, {'slug': 'mistral-large-4'}, 0, (1.0, None, 3.0, 'usd'), None)
        cle = lambda i: (i[3], 0 if i[4][3] == 'aucune' else 2, 0.0, float('inf'), 1)
        self.semer('mistral', ['mistral-large-4'], 'redaction_mail', 40, 39)
        with patch.object(routage5695, 'mode', return_value='actif'):
            r = routage5695.evaluer(self.desk.c, [local, api], 'redaction_mail', 60, cle, local)
            self.assertIs(r['choix'], local)                       # 87,5 au banc d'évaluation, moins de 15 points sous Mistral (97,5) : le local reste
            self.assertIn('score 88 au banc', r['raison'])
            self.rapport({'modele': 'modele-local', 'versions': {'5.6.97': {'synthese': {'cas': 24, 'regle_respectee': 0.5}}}})
            self.assertIs(routage5695.evaluer(self.desk.c, [local, api], 'redaction_mail', 60, cle, local)['choix'], api)   # 50 : sous le seuil
            self.assertIs(routage5695.evaluer(self.desk.c, [local, api], 'classement', 60, cle, local)['choix'], local)    # autre étape : rapport sans effet

    def test_G05_rapport_mal_forme_ignore(self):
        for contenu in ('pas du json', {'modele': 'x y', 'versions': {'a': {'synthese': {'cas': 3, 'regle_respectee': 0.9}}}},
                        {'modele': 'modele-local', 'versions': {'a': {'synthese': {'cas': 3, 'regle_respectee': 7}}}}, {'modele': 'modele-local', 'versions': {}}):
            self.rapport(contenu)
            self.assertIsNone(routage5695.evaluation(self.state), contenu)


if __name__ == '__main__':
    unittest.main()
