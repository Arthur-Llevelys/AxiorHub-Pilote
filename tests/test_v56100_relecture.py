"""5.6.100 (lot B · P1, R02, R03, R06, R09, V100-03) : objet commun « À relire », lecteur commun et décisions sûres.

Données entièrement fictives (dossiers DOS-001 et DOS-RES, adresses example.test) ; faux service IMAP ; aucun réseau, aucun modèle."""
from datetime import datetime, timedelta, timezone
from email import policy
from email.message import EmailMessage
import io
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

import test_agent as fixtures
from agent import membres5650, relecture56100 as rl, reponses5671 as rep
from agent.common import Stop, private_json
from agent.desk import Desk

ADMIN = ('cabinet', 'administrateur')
TITULAIRE = ('titulaire@example.test', 'avocat')
AVOCAT_B = ('avocat-b@example.test', 'avocat')
ASSISTANT = ('assistant@example.test', 'assistant')
RESERVE = 'DOS-RES'
ORIGINE = 'https://cabinet.example.test'
JETON = 'jeton-csrf-fictif'
RACINE = Path(__file__).resolve().parents[1]


def cle(n):
    return '%064x' % (0xabc0000 + n)


class Base(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.EngineTests('test_observation_has_no_mail_write')
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.addCleanup(membres5650.clear)
        matters = self.f.matters + [{'id': RESERVE, 'client_name': 'Client ZORVAL', 'path': '/Dossiers/ZORVAL', 'references': [RESERVE],
                                     'aliases': [], 'correspondents': []}]
        private_json(self.f.matter_file, matters)
        self.desk = Desk(self.f.c)
        self.addCleanup(self.desk.db.close)
        membres5650.set_reserve(self.f.c['state_dir'], RESERVE, ['titulaire@example.test'])
        (Path(self.f.c['state_dir']) / 'reports').mkdir(exist_ok=True)
        self.t0 = datetime(2026, 11, 1, 8, 0, tzinfo=timezone.utc)

    def proposition(self, n, matter='', sujet='Demande de calendrier', corps=None, compact=True, minutes=None, requires=True):
        k = cle(n)
        cree = (self.t0 + timedelta(minutes=n if minutes is None else minutes)).isoformat()
        data = {'result': {'body': corps or 'Bonjour,\nProjet de réponse %d.' % n, 'requires_decision': requires, 'limits': ['À relire.']},
                'verification': None, 'matter': matter, 'matter_path': '', 'key': k, 'source_hash': 'h', 'created_at': cree,
                'recipients': ['client@example.test'], 'sources': [{'id': 'incoming', 'title': 'Courriel reçu'}]}
        private_json(Path(self.f.c['state_dir']) / 'reports' / (k + '.json'),
                     {'subject': sujet, 'sender': 'client@example.test', 'matter': matter, 'received_at': cree})
        texte = json.dumps(data, separators=(',', ':')) if compact else json.dumps(data, indent=1)
        self.desk.db.execute('INSERT OR REPLACE INTO manual_drafts VALUES(?,?,?)', (k, texte, cree))
        self.desk.db.commit()
        rl.projeter_cle(self.desk, k)
        return k

    def ident(self, k):
        row = self.desk.db.execute('SELECT data FROM manual_drafts WHERE mail_key=?', (k,)).fetchone()
        return rl.id_proposition(k, json.loads(row[0]))

    def toutes(self, acteur, **args):
        out, curseur = [], None
        for _ in range(50):
            page = rl.liste(self.desk, acteur, {**args, **({'curseur': curseur} if curseur else {})}, '/p')
            out += page['lignes']
            curseur = page['curseur']
            if not curseur:
                return out
        self.fail('pagination sans fin')

    def cmd(self, acteur, ident, sous, **data):
        data.setdefault('cle', 'cle-%016d' % int(time.time() * 1e6 % 1e15))
        return rl.commande(self.desk, acteur, ident, sous, data)

    def compter(self, table):
        return self.desk.db.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0]


# ---------------------------------------------------------------------------------------------------- R06 : liste complète et cohérente
class Liste(Base):
    def test_R06_203_propositions_200_masquees_json_compact(self):
        for n in range(3):
            self.proposition(n, sujet='Visible %d' % n, compact=(n == 0))          # le plus ancien en JSON compact
        for n in range(3, 203):
            self.proposition(n, matter=RESERVE, sujet='Confidentiel %d' % n)      # 200 plus récents, réservés
        page = rl.liste(self.desk, AVOCAT_B, {}, '/p')
        self.assertEqual([x['objet'] for x in page['lignes']], ['Visible 2', 'Visible 1', 'Visible 0'])
        self.assertEqual((page['total'], page['total_exact'], page['curseur']), (3, True, None))
        self.assertNotIn('Confidentiel', json.dumps(page, ensure_ascii=False))
        self.assertEqual(rl.liste(self.desk, ADMIN, {}, '/p')['total'], 203)
        self.assertEqual(len(self.toutes(TITULAIRE)), 203)

    def test_R06_mutation_garde_retiree_detectee(self):
        """Contre-épreuve : sans le garde (mutation en mémoire), la page de l'avocat B montrerait des projets réservés."""
        for n in range(3):
            self.proposition(n, sujet='Visible %d' % n)
        self.proposition(9, matter=RESERVE, sujet='Confidentiel 9')
        with patch.object(rl, 'visibles', lambda desk, acteur, lignes: {x['id'] for x in lignes}), \
                patch('agent.acces5697.Perimetre.sql', lambda self, col: ('1=1', ())):
            fuite = rl.liste(self.desk, AVOCAT_B, {}, '/p')
        self.assertIn('Confidentiel 9', [x['objet'] for x in fuite['lignes']])
        self.assertNotIn('Confidentiel 9', [x['objet'] for x in rl.liste(self.desk, AVOCAT_B, {}, '/p')['lignes']])

    def test_R06_deux_pages_sans_trou_ordre_stable_et_droits_apres_changement_de_page(self):
        for n in range(70):
            self.proposition(n, matter=RESERVE if n % 7 == 0 else '', sujet='Projet %02d' % n, minutes=n // 3)   # dates en double : tri par id
        vues = self.toutes(AVOCAT_B, limite='30')
        attendus = [r['id'] for r in self.desk.db.execute(
            "SELECT id FROM file56100_items WHERE matter<>? ORDER BY updated DESC,id DESC", (RESERVE,))]
        self.assertEqual([x['id'] for x in vues], attendus)
        self.assertEqual(len(set(x['id'] for x in vues)), 60)
        p1 = rl.liste(self.desk, AVOCAT_B, {'limite': '25'}, '/p')
        self.assertEqual(p1['total'], 60)
        with self.assertRaises(rl.Refus) as ctx:                               # curseur lié au filtre, à la recherche et au compte
            rl.liste(self.desk, AVOCAT_B, {'limite': '25', 'curseur': p1['curseur'], 'q': 'projet'}, '/p')
        self.assertEqual(ctx.exception.status, 422)
        with self.assertRaises(rl.Refus):
            rl.liste(self.desk, TITULAIRE, {'limite': '25', 'curseur': p1['curseur']}, '/p')
        membres5650.set_reserve(self.f.c['state_dir'], 'DOS-001', ['titulaire@example.test'])   # droits changés entre deux pages
        self.desk.db.execute("UPDATE file56100_items SET matter='DOS-001' WHERE titre IN ('Projet 01','Projet 02')")
        self.desk.db.commit()
        p2 = rl.liste(self.desk, AVOCAT_B, {'limite': '25', 'curseur': p1['curseur']}, '/p')
        self.assertFalse({'Projet 01', 'Projet 02'} & {x['objet'] for x in p2['lignes']})
        self.assertFalse({x['id'] for x in p1['lignes']} & {x['id'] for x in p2['lignes']})

    def test_R06_recherche_accentuee_avant_la_limite(self):
        self.proposition(1, sujet='Échéance du délibéré')
        self.proposition(2, sujet='Calendrier')
        self.proposition(3, sujet='ECHEANCE réservée', matter=RESERVE)
        for q in ('echeance', 'ÉCHÉANCE', '  échéance  '):
            page = rl.liste(self.desk, AVOCAT_B, {'q': q}, '/p')
            self.assertEqual([x['objet'] for x in page['lignes']], ['Échéance du délibéré'], q)
            self.assertEqual(page['total'], 1)
        self.assertEqual(rl.liste(self.desk, AVOCAT_B, {'q': 'client@example'}, '/p')['total'], 2)   # destinataire autorisé

    def test_R06_json_illisible_isole_et_compte_jamais_aucun_resultat(self):
        self.proposition(1, sujet='Lisible')
        k = cle(50)
        self.desk.db.execute('INSERT INTO manual_drafts VALUES(?,?,?)', (k, '{illisible', self.t0.isoformat()))
        self.desk.db.commit()
        rl.rattraper(self.desk)
        self.assertEqual(rl.anomalies(self.desk), 1)
        page = rl.liste(self.desk, ADMIN, {}, '/p')
        self.assertEqual(page['total'], 1)
        self.assertEqual(page['sources']['local']['anomalies'], 1)
        self.assertNotIn(' LIKE ', Path(rl.__file__).read_text(encoding='utf-8').upper())   # migration sans LIKE sur le JSON

    def test_R06_rattrapage_relancable_et_disparitions(self):
        k1 = self.proposition(1)
        k2 = self.proposition(2)
        self.desk.db.execute('DELETE FROM file56100_items')
        self.desk.db.commit()
        self.desk.setting('relecture56100:filigrane', None)
        self.assertEqual(rl.rattraper(self.desk, 1)['projetes'], 1)               # par lots, relançable
        self.assertEqual(rl.rattraper(self.desk, 1)['projetes'], 1)
        self.assertEqual(rl.liste(self.desk, ADMIN, {}, '/p')['total'], 2)
        self.desk.db.execute('DELETE FROM manual_drafts WHERE mail_key=?', (k1,))   # retiré du producteur hors du lecteur (Gmail, relance)
        self.desk.db.commit()
        rl.rattraper(self.desk)
        self.assertEqual([x['id'] for x in rl.liste(self.desk, ADMIN, {}, '/p')['lignes']], [self.ident(k2)])

    def test_R05_aucune_ecriture_pendant_les_lectures(self):
        k = self.proposition(1)
        ident = self.ident(k)
        dml = []
        # écritures métier seulement : le registre des affaires (dossiers5697, 5.6.97) prépare encore ses tables à la première lecture
        self.desk.db.set_trace_callback(lambda sql: dml.append(sql) if re.match(r'\s*(INSERT|UPDATE|DELETE|REPLACE)\b', sql, re.I) else None)
        try:
            rl.liste(self.desk, AVOCAT_B, {}, '/p')
            d = rl.detail(self.desk, AVOCAT_B, ident, '/p')
            rl.section(self.desk, AVOCAT_B, ident, d['revision'], 'sources')
            rl.page_html(self.desk, '/p', AVOCAT_B, {'item': ident})
        finally:
            self.desk.db.set_trace_callback(None)
        self.assertEqual(dml, [])
        self.assertEqual(rl.liste(self.desk, AVOCAT_B, {}, '/p')['total'], 1)


# ---------------------------------------------------------------------------------------------------- R04 : droits sur l'objet
class Droits(Base):
    def test_R04_meme_404_pour_absent_et_interdit_avant_tout_effet(self):
        k = self.proposition(1, matter=RESERVE, sujet='Confidentiel')
        ident = self.ident(k)
        rev = rl.detail(self.desk, TITULAIRE, ident, '/p')['revision']
        for acteur in (AVOCAT_B,):
            for cible in (ident, 'p-' + '0' * 32):
                with self.assertRaises(rl.Refus) as ctx:
                    rl.detail(self.desk, acteur, cible, '/p')
                self.assertEqual((ctx.exception.status, ctx.exception.code), (404, 'introuvable'))
                with self.assertRaises(rl.Refus) as ctx:
                    rl.section(self.desk, acteur, cible, rev, 'message')
                self.assertEqual(ctx.exception.status, 404)
                with self.assertRaises(rl.Refus) as ctx:
                    self.cmd(acteur, cible, 'decision', action='ecarter', revision=rev)
                self.assertEqual(ctx.exception.status, 404)
        self.assertEqual((self.compter('file56100_commandes'), self.compter('file56100_historique')), (0, 0))
        self.assertIsNotNone(self.desk.db.execute('SELECT 1 FROM manual_drafts WHERE mail_key=?', (k,)).fetchone())

    def test_R04_droits_retires_entre_liste_et_action(self):
        k = self.proposition(1, matter='DOS-001')
        ident = self.ident(k)
        rev = rl.detail(self.desk, AVOCAT_B, ident, '/p')['revision']
        membres5650.set_reserve(self.f.c['state_dir'], 'DOS-001', ['titulaire@example.test'])
        with self.assertRaises(rl.Refus) as ctx:
            self.cmd(AVOCAT_B, ident, 'decision', action='retenir', revision=rev, corps='Texte')
        self.assertEqual(ctx.exception.status, 404)
        self.assertEqual(self.compter('file56100_commandes'), 0)

    def test_R04_rattachement_du_message_compte_meme_si_le_projet_est_sans_dossier(self):
        k = self.proposition(1, matter='', sujet='Rattaché ailleurs')
        rapport = Path(self.f.c['state_dir']) / 'reports' / (k + '.json')
        private_json(rapport, {**json.loads(rapport.read_text(encoding='utf-8')), 'matter': RESERVE})   # le serveur rattache le message
        self.assertEqual(rl.liste(self.desk, AVOCAT_B, {}, '/p')['total'], 0)
        self.assertEqual(rl.liste(self.desk, TITULAIRE, {}, '/p')['total'], 1)

    def test_R04_controle_en_panne_503_jamais_elargi(self):
        self.proposition(1)
        with patch('agent.membres5650.load', side_effect=Stop('registre_acces_illisible')):
            with self.assertRaises(rl.Refus) as ctx:
                rl.liste(self.desk, AVOCAT_B, {}, '/p')
        self.assertEqual(ctx.exception.status, 503)

    def test_R04_role_assistant_reporte_mais_ne_decide_pas(self):
        k = self.proposition(1)
        ident = self.ident(k)
        d = rl.detail(self.desk, ASSISTANT, ident, '/p')
        self.assertEqual(d['actions'], ['reporter'])
        with self.assertRaises(rl.Refus) as ctx:
            self.cmd(ASSISTANT, ident, 'decision', action='ecarter', revision=d['revision'])
        self.assertEqual(ctx.exception.status, 403)
        jusqu = int(time.time()) + 3600
        self.assertEqual(self.cmd(ASSISTANT, ident, 'decision', action='reporter', revision=d['revision'], jusqu_a=jusqu)['etat'], 'reporte')


# ---------------------------------------------------------------------------------------------------- R09 et P1 : décisions sûres
class Decisions(Base):
    def test_R09_ecarter_A_quand_B_existe_409_et_B_conserve(self):
        k = self.proposition(1, corps='Version A')
        ident = self.ident(k)
        rev_a = rl.detail(self.desk, ADMIN, ident, '/p')['revision']
        rev_b = self.cmd(TITULAIRE, ident, 'enregistrer', revision=rev_a, corps='Version B')['revision']   # second onglet
        with self.assertRaises(rl.Refus) as ctx:
            self.cmd(ADMIN, ident, 'decision', action='ecarter', revision=rev_a)
        self.assertEqual(ctx.exception.status, 409)
        with self.assertRaisesRegex(Stop, 'version_perimee'):                    # ancienne action, même contrôle
            rep.ecarter(self.desk, 'cabinet', k, revision=rev_a)
        corps = json.loads(self.desk.db.execute('SELECT data FROM manual_drafts WHERE mail_key=?', (k,)).fetchone()[0])['result']['body']
        self.assertEqual(corps, 'Version B')
        self.assertEqual(self.cmd(ADMIN, ident, 'decision', action='ecarter', revision=rev_b)['etat'], 'ecarte')
        self.assertIsNone(self.desk.db.execute('SELECT 1 FROM manual_drafts WHERE mail_key=?', (k,)).fetchone())
        trace = json.loads(self.desk.db.execute("SELECT donnees FROM file56100_historique WHERE action='ecarter'").fetchone()[0])
        self.assertEqual(trace['corps'], 'Version B')                           # trace et historique, le courriel reçu n'est pas touché
        self.assertEqual(rl.detail(self.desk, ADMIN, ident, '/p')['etat'], 'ecarte')

    def test_R09_nouvelle_preparation_remplace_l_ancien_projet(self):
        k = self.proposition(1, corps='Première préparation')
        ancien = self.ident(k)
        rev = rl.detail(self.desk, ADMIN, ancien, '/p')['revision']
        self.proposition(1, corps='Relance avec une autre IA', minutes=30)       # même courriel, nouvelle préparation
        nouveau = self.ident(k)
        self.assertNotEqual(ancien, nouveau)
        with self.assertRaises(rl.Refus) as ctx:
            self.cmd(ADMIN, ancien, 'decision', action='ecarter', revision=rev)
        self.assertEqual(ctx.exception.status, 409)
        d = rl.detail(self.desk, ADMIN, ancien, '/p')
        self.assertEqual((d['etat'], d['remplace_par']), ('remplace', nouveau))
        self.assertEqual([x['id'] for x in rl.liste(self.desk, ADMIN, {}, '/p')['lignes']], [nouveau])

    def test_R09_cle_d_idempotence_meme_recu_ou_409(self):
        k = self.proposition(1)
        ident = self.ident(k)
        rev = rl.detail(self.desk, ADMIN, ident, '/p')['revision']
        c = 'cle-idempotence-0001'
        r1 = self.cmd(ADMIN, ident, 'decision', action='retenir', revision=rev, corps='Relu', cle=c)
        r2 = self.cmd(ADMIN, ident, 'decision', action='retenir', revision=rev, corps='Relu', cle=c)
        self.assertEqual(r1, r2)
        with self.assertRaises(rl.Refus) as ctx:
            self.cmd(ADMIN, ident, 'decision', action='retenir', revision=rev, corps='Autre texte', cle=c)
        self.assertEqual((ctx.exception.status, ctx.exception.code), (409, 'cle_reutilisee'))
        self.assertEqual(self.compter('file56100_commandes'), 1)

    def test_P1_retenir_n_envoie_rien_et_reste_accessible(self):
        k = self.proposition(1)
        ident = self.ident(k)
        rev = rl.detail(self.desk, ADMIN, ident, '/p')['revision']
        with patch('agent.mailbox.Mailbox', side_effect=AssertionError('aucune connexion de messagerie pour Retenir')), \
                patch('agent.intelligence.deposit_draft', side_effect=AssertionError('aucun dépôt pour Retenir')):
            recu = self.cmd(ADMIN, ident, 'decision', action='retenir', revision=rev, corps='Texte relu par l’avocat')
        self.assertEqual(recu['etat'], 'retenu')
        self.assertEqual(rl.liste(self.desk, ADMIN, {}, '/p')['total'], 0)
        d = rl.detail(self.desk, ADMIN, ident, '/p')
        self.assertEqual((d['etat'], d['corps'], d['actions']), ('retenu', 'Texte relu par l’avocat', ['deposer']))

    def test_P1_enregistrer_rend_id_revision_corps_et_reste_a_relire(self):
        k = self.proposition(1)
        ident = self.ident(k)
        rev = rl.detail(self.desk, ADMIN, ident, '/p')['revision']
        recu = self.cmd(ADMIN, ident, 'enregistrer', revision=rev, corps='Saisie en cours')
        self.assertEqual((recu['id'], recu['corps'], recu['etat']), (ident, 'Saisie en cours', 'enregistre'))
        self.assertNotEqual(recu['revision'], rev)
        d = rl.detail(self.desk, ADMIN, ident, '/p')
        self.assertEqual((d['revision'], d['etat']), (recu['revision'], 'a_relire'))
        self.assertEqual(rl.liste(self.desk, ADMIN, {}, '/p')['total'], 1)

    def test_P1_double_depot_un_seul_brouillon_sans_envoi(self):
        k = self.proposition(1)
        ident = self.ident(k)
        rev = rl.detail(self.desk, ADMIN, ident, '/p')['revision']
        appels = []

        def depot(desk, args):
            appels.append(dict(args))
            desk.db.execute('DELETE FROM manual_drafts WHERE mail_key=?', (args['key'],))
            desk.db.commit()
            return {'brouillon_imap': 'verifie', 'dossier': 'Drafts', 'uid': '41', 'envoi': False}
        c = 'cle-depot-000000001'
        with patch('agent.intelligence.deposit_draft', side_effect=depot):
            r1 = self.cmd(ADMIN, ident, 'deposer', revision=rev, corps='Texte relu', cle=c)
            r2 = self.cmd(ADMIN, ident, 'deposer', revision=rev, corps='Texte relu', cle=c)          # double clic
            with self.assertRaises(rl.Refus) as ctx:
                self.cmd(ADMIN, ident, 'deposer', revision=rev, corps='Texte relu', cle='cle-depot-000000002')   # autre onglet
        self.assertEqual(ctx.exception.status, 409)
        self.assertEqual(len(appels), 1)
        self.assertEqual(appels[0], {'key': k, 'confirm': 'yes'})
        self.assertEqual((r1, r1['etat'], r1['uid']), (r2, 'depose', '41'))
        self.assertEqual(rl.detail(self.desk, ADMIN, ident, '/p')['etat'], 'depose')

    def test_P1_depot_incertain_a_verifier_jamais_rejoue(self):
        k = self.proposition(1)
        ident = self.ident(k)
        rev = rl.detail(self.desk, ADMIN, ident, '/p')['revision']
        appels = []

        def doute(desk, args):
            appels.append(1)
            raise Stop('append_imap_incertain')
        with patch('agent.intelligence.deposit_draft', side_effect=doute):
            r1 = self.cmd(ADMIN, ident, 'deposer', revision=rev)
            r2 = self.cmd(ADMIN, ident, 'deposer', revision=rev, cle='cle-depot-000000003')
        self.assertEqual((r1['etat'], r2['etat'], len(appels)), ('a_verifier', 'a_verifier', 1))
        self.assertEqual(rl.liste(self.desk, ADMIN, {}, '/p')['total'], 1)          # aucune disparition sans preuve
        self.assertEqual(rl.detail(self.desk, ADMIN, ident, '/p')['etat'], 'a_relire')

    def test_P1_depot_refuse_laisse_le_projet_et_libere_l_intention(self):
        k = self.proposition(1)
        ident = self.ident(k)
        rev = rl.detail(self.desk, ADMIN, ident, '/p')['revision']
        with patch('agent.intelligence.deposit_draft', side_effect=Stop('courriel_modifie_depuis_projet')):
            with self.assertRaises(rl.Refus) as ctx:
                self.cmd(ADMIN, ident, 'deposer', revision=rev, corps='Texte relu')
        self.assertEqual(ctx.exception.status, 409)
        self.assertEqual(rl.detail(self.desk, ADMIN, ident, '/p')['revision'], rev)   # texte d'origine restauré par le producteur
        self.assertEqual(self.desk.db.execute("SELECT etat FROM file56100_intentions").fetchone()[0], 'echec')

    def test_P1_report_par_compte_et_fuseau(self):
        from agent.settings568 import profile
        k = self.proposition(1)
        ident = self.ident(k)
        rev = rl.detail(self.desk, ADMIN, ident, '/p')['revision']
        self.desk.setting('proactive568:profile:titulaire@example.test', {**profile(self.desk, 'titulaire@example.test'), 'timezone': 'America/Martinique'})
        maintenant = int(time.time())
        jusqu = maintenant + 7200
        recu = self.cmd(TITULAIRE, ident, 'decision', action='reporter', revision=rev, jusqu_a=jusqu)
        attendu = datetime.fromtimestamp(jusqu, timezone.utc).astimezone(__import__('zoneinfo').ZoneInfo('America/Martinique')).strftime('%d/%m/%Y à %H:%M')
        self.assertEqual(recu['jusqu_a_local'], attendu)
        self.assertEqual(rl.liste(self.desk, TITULAIRE, {}, '/p')['total'], 0)        # masqué pour lui seul
        self.assertEqual(rl.liste(self.desk, ADMIN, {}, '/p')['total'], 1)
        self.assertEqual(rl.liste(self.desk, TITULAIRE, {}, '/p', maintenant=jusqu)['total'], 1)   # réapparaît à l'heure choisie
        self.cmd(ADMIN, ident, 'enregistrer', revision=rev, corps='Nouvelle version')    # ou plus tôt si la version change
        self.assertEqual(rl.liste(self.desk, TITULAIRE, {}, '/p')['total'], 1)
        for mauvais in (maintenant - 5, maintenant + 400 * 86400, '1800000000'):
            with self.assertRaises(rl.Refus):
                self.cmd(TITULAIRE, ident, 'decision', action='reporter', revision=rev, jusqu_a=mauvais)

    def test_V100_03_dix_decisions_a_la_suite_sans_effet_externe(self):
        for n in range(10):
            self.proposition(n, sujet='Courriel %d' % n)
        with patch('agent.mailbox.Mailbox', side_effect=AssertionError('aucune connexion de messagerie')):
            for n in range(10):
                premiere = rl.liste(self.desk, ADMIN, {}, '/p')['lignes'][0]
                d = rl.detail(self.desk, ADMIN, premiere['id'], '/p')
                action = ('retenir', 'ecarter', 'reporter')[n % 3]
                extra = {'corps': d['corps'] + ' (relu)'} if action == 'retenir' else ({'jusqu_a': int(time.time()) + 86400} if action == 'reporter' else {})
                self.assertIn(self.cmd(ADMIN, d['id'], 'decision', action=action, revision=d['revision'], **extra)['etat'],
                              ('retenu', 'ecarte', 'reporte'))
        self.assertEqual(rl.liste(self.desk, ADMIN, {}, '/p')['total'], 0)
        self.assertEqual(self.compter('file56100_commandes'), 10)


# ---------------------------------------------------------------------------------------------------- anciennes actions (même contrôle)
class AnciennesActions(Base):
    def env(self, data, compte='cabinet', role='administrateur'):
        brut = json.dumps(data).encode()
        return {'HTTP_ORIGIN': ORIGINE, 'HTTP_X_CSRF_TOKEN': JETON, 'CONTENT_TYPE': 'application/json', 'CONTENT_LENGTH': str(len(brut)),
                'wsgi.input': io.BytesIO(brut), 'HTTP_X_AXIORHUB_USER': compte, 'HTTP_X_AXIORHUB_ROLE': role, 'REQUEST_METHOD': 'POST'}

    def test_R09_anciennes_routes_m5671_exigent_la_version_affichee(self):
        from agent import web440
        k = self.proposition(1, corps='Version A')
        data_a = json.loads(self.desk.db.execute('SELECT data FROM manual_drafts WHERE mail_key=?', (k,)).fetchone()[0])
        rev_a = rl.revision_proposition(data_a)
        rl.commande(self.desk, ADMIN, self.ident(k), 'enregistrer', {'revision': rev_a, 'corps': 'Version B', 'cle': 'cle-onglet-b-000001'})
        auth = {'origin': ORIGINE, 'csrf': JETON}
        for donnees in ({'key': k, 'revision': rev_a}, {'key': k}):
            out = web440.api(self.env(donnees), self.desk, auth, '', 'm5671/reponse/ecarter', {}, 'POST')
            self.assertTrue(out['status'].startswith('409'), out['status'])
        with patch('agent.intelligence.deposit_draft', side_effect=AssertionError('aucun dépôt d’une version remplacée')):
            out = web440.api(self.env({'key': k, 'revision': rev_a, 'corps': 'Texte A'}), self.desk, auth, '', 'm5671/reponse/deposer', {}, 'POST')
        self.assertTrue(out['status'].startswith('409'), out['status'])
        self.assertIn('Version B', self.desk.db.execute('SELECT data FROM manual_drafts WHERE mail_key=?', (k,)).fetchone()[0])
        rev_b = rl.revision_courante(self.desk, k)
        out = web440.api(self.env({'key': k, 'revision': rev_b}), self.desk, auth, '', 'm5671/reponse/ecarter', {}, 'POST')
        self.assertTrue(out['status'].startswith('200'), out['body'])
        self.assertEqual(self.desk.db.execute("SELECT etat FROM file56100_items").fetchone()[0], 'ecarte')

    def test_R09_mutation_sans_controle_de_revision_detectee(self):
        """Contre-épreuve (mutation en mémoire) : sans le contrôle de révision, l'ancienne route efface la version B (défaut de la 5.6.99)."""
        from agent import web440
        k = self.proposition(1, corps='Version A')
        rev_a = rl.revision_courante(self.desk, k)
        rl.commande(self.desk, ADMIN, self.ident(k), 'enregistrer', {'revision': rev_a, 'corps': 'Version B', 'cle': 'cle-onglet-b-000002'})
        with patch.object(rep, '_verifier_revision', lambda data, revision: None):
            out = web440.api(self.env({'key': k, 'revision': rev_a}), self.desk, {'origin': ORIGINE, 'csrf': JETON}, '', 'm5671/reponse/ecarter', {}, 'POST')
        self.assertTrue(out['status'].startswith('200'), out['status'])
        self.assertIsNone(self.desk.db.execute('SELECT 1 FROM manual_drafts WHERE mail_key=?', (k,)).fetchone())

    def test_R09_formulaires_historiques_portent_la_revision(self):
        k = self.proposition(1)
        data = json.loads(self.desk.db.execute('SELECT data FROM manual_drafts WHERE mail_key=?', (k,)).fetchone()[0])
        html = rep.editor_html(k, data['result'], None, revision=rl.revision_proposition(data))
        self.assertEqual(html.count('name="revision" value="%s"' % rl.revision_proposition(data)), html.count('name="key"'))
        cartes = rep.queue_items(self.desk, '/p')
        self.assertEqual(cartes[0]['relecture'], self.ident(k))                       # identifiant stable de la file commune (file5657, Produire)
        self.assertEqual(cartes[0]['choices'][1]['fields']['revision'], rl.revision_proposition(data))


# ---------------------------------------------------------------------------------------------------- HTTP : routes, rôles, en-têtes
class Routes(Base):
    def appel(self, nom, methode='GET', data=None, args=None, compte='cabinet', role='administrateur', jeton=JETON):
        from agent import web440
        brut = json.dumps(data or {}).encode()
        env = {'HTTP_ORIGIN': ORIGINE, 'HTTP_X_CSRF_TOKEN': jeton, 'CONTENT_TYPE': 'application/json', 'CONTENT_LENGTH': str(len(brut)),
               'wsgi.input': io.BytesIO(brut), 'HTTP_X_AXIORHUB_USER': compte, 'HTTP_X_AXIORHUB_ROLE': role, 'REQUEST_METHOD': methode}
        out = web440.api(env, self.desk, {'origin': ORIGINE, 'csrf': JETON}, '/p', nom, args or {}, methode)
        return out, json.loads(out['body'])

    def test_P1_routes_roles_en_tetes_et_csrf(self):
        from agent import shell501, standalone_auth, web440
        self.assertTrue(standalone_auth.allowed('assistant', 'POST', '/api440/m56100/relecture/p-abc/decision'))
        self.assertFalse(standalone_auth.allowed('avocat', 'POST', '/api440/m56100/relecture/p-abc/envoyer'))
        self.assertFalse(standalone_auth.allowed('avocat', 'POST', '/api440/m56100/relecture'))
        self.assertFalse(rl.autoriser_route('lecteur', 'GET', 'm56100/relecture'))
        self.assertIn('/static/v56100-lecteur.js', web440.STATIC)
        self.assertIn('v56100-lecteur.css', shell501.BUNDLE)
        self.assertIn('ax100-relecture', (RACINE / 'agent/static/app520.css').read_text(encoding='utf-8'))
        k = self.proposition(1, sujet='Objet <b>fictif</b>')
        ident = self.ident(k)
        out, corps = self.appel('m56100/relecture', args={'limite': '30'})
        self.assertEqual(out['status'], '200 OK')
        self.assertIn(('Cache-Control', 'private, no-store'), out['headers'])
        self.assertEqual(corps['lignes'][0]['objet'], 'Objet <b>fictif</b>')     # données JSON ; échappées à l'affichage (textContent)
        out, d = self.appel('m56100/relecture/' + ident)
        self.assertEqual((out['status'], d['id']), ('200 OK', ident))
        out, s = self.appel('m56100/relecture/%s/sources' % ident, args={'revision': d['revision']})
        self.assertEqual(s['sources'][0]['titre'], 'Courriel reçu')
        out, err = self.appel('m56100/relecture/%s/sources' % ident, args={'revision': 'rperime'})
        self.assertTrue(out['status'].startswith('409'))
        out, err = self.appel('m56100/relecture/%s/decision' % ident, 'POST', {'action': 'ecarter', 'revision': d['revision'], 'cle': 'cle-http-0000000001'},
                              jeton='autre')
        self.assertTrue(out['status'].startswith('403'))                                # CSRF
        out, err = self.appel('m56100/relecture/%s/decision' % ident, 'POST', {'action': 'ecarter', 'revision': d['revision'], 'cle': 'cle-http-0000000001'},
                              compte='avocat-b@example.test', role='avocat')
        self.assertEqual(out['status'], '200 OK')
        out, err = self.appel('m56100/relecture/%s/decision' % ident, 'POST', {'action': 'ecarter', 'revision': d['revision'], 'cle': 'cle-http-0000000002'})
        self.assertEqual((out['status'], err['error']), ('409 Conflict', 'version_perimee'))
        k2 = self.proposition(2, matter=RESERVE)
        a, b = self.appel('m56100/relecture/' + self.ident(k2), compte='avocat-b@example.test', role='avocat'), \
            self.appel('m56100/relecture/p-' + '1' * 32, compte='avocat-b@example.test', role='avocat')
        self.assertEqual((a[0]['status'], a[1]), (b[0]['status'], b[1]))               # même 404 neutre
        self.assertTrue(a[0]['status'].startswith('404'))

    def test_P1_page_courriels_nouvelle_et_ancienne_presentation(self):
        from agent import interface56100, web440
        k = self.proposition(1, sujet='Calendrier <script>')
        ident = self.ident(k)
        env = {'HTTP_X_AXIORHUB_USER': 'cabinet', 'HTTP_X_AXIORHUB_ROLE': 'administrateur', 'REQUEST_METHOD': 'GET'}
        with patch('agent.drafts440.list_drafts', side_effect=AssertionError('aucune lecture IMAP au rendu de la page')):
            html = web440.drafts_page(self.desk, {'csrf': JETON}, '/p', {'item': ident}, env)
        self.assertIn('data-ax100-relecture', html)
        self.assertIn('/p/courriels?item=' + ident, html)                            # repli sans script, adresse restaurable
        self.assertIn('/static/v56100-lecteur.js', html)
        self.assertIn('Calendrier &lt;script&gt;', html)
        self.assertNotIn('<script>', re.sub(r'<script[^>]*src="[^"]+"[^>]*></script>', '', html.split('<body>', 1)[-1]).replace('<script type="application/json"', ''))
        self.assertIn('/p/mail?key=' + k, html)                                       # « Ouvrir en pleine page »
        interface56100.regler_compte(self.desk, 'cabinet', False)
        with patch('agent.drafts440.list_drafts', return_value={'items': [], 'uidvalidity': '7', 'folder': 'Drafts'}):
            ancien = web440.drafts_page(self.desk, {'csrf': JETON}, '/p', {}, env)
        self.assertIn('id="ax-drafts"', ancien)
        self.assertNotIn('data-ax100-relecture', ancien)

    def test_P1_lecteur_html_ids_distincts_par_instance(self):
        a, b = rl.lecteur_html('/p', ''), rl.lecteur_html('/p', 'acc')
        self.assertIn('id="ax100-texte"', a)
        self.assertIn('id="ax100-textacc"'.replace('textacc', 'texteacc'), b)
        self.assertNotIn('<script', a + b)
        for action in ('enregistrer', 'retenir', 'ecarter', 'reporter', 'deposer'):
            self.assertIn('data-action="%s"' % action, a)
        self.assertNotIn('envoyer', a.lower().replace('rien n’est envoyé', ''))


# ---------------------------------------------------------------------------------------------------- brouillons IMAP (appel séparé)
class Brouillons(Base):
    def setUp(self):
        super().setUp()
        from fake_imap440 import FakeIMAP
        self.imap = FakeIMAP
        FakeIMAP.reset()
        secret = Path(self.f.t.name) / 'secret-imap'
        secret.write_text('mot-de-passe-fictif')
        secret.chmod(0o600)
        self.f.c['mail']['password_file'] = str(secret)
        p = patch('agent.mailbox.imaplib.IMAP4_SSL', FakeIMAP)
        p.start()
        self.addCleanup(p.stop)

    def brouillon(self, sujet='Re: calendrier', corps='Cher Confrère,\n\nBien reçu.', mid='<brouillon-1@example.test>', repond_a=''):
        m = EmailMessage(policy=policy.SMTP)
        m['From'], m['To'], m['Subject'], m['Message-ID'] = 'cabinet@example.test', 'confrere@example.test', sujet, mid
        if repond_a:
            m['In-Reply-To'] = repond_a
        m['Date'] = 'Fri, 02 Oct 2026 08:00:00 +0200'
        m.set_content(corps)
        return self.imap.add('Drafts', m.as_bytes(policy=policy.SMTP), ['\\Draft'])

    def test_P1_brouillons_listes_a_part_enregistrer_puis_ecarter_localement(self):
        self.proposition(1, sujet='Proposition locale')
        self.brouillon()
        page = rl.liste(self.desk, ADMIN, {'source': 'brouillon'}, '/p')
        self.assertEqual(len(page['lignes']), 1)
        ident = page['lignes'][0]['id']
        self.assertEqual((page['lignes'][0]['type'], page['sources']['imap']['etat']), ('imap', 'a_jour'))
        d = rl.detail(self.desk, ADMIN, ident, '/p')
        self.assertIn('Bien reçu', d['corps'])
        recu = self.cmd(ADMIN, ident, 'enregistrer', revision=d['revision'], corps='Cher Confrère,\n\nBien reçu, merci.')
        self.assertEqual(recu['etat'], 'enregistre')
        self.assertNotEqual(recu['revision'], d['revision'])                          # nouvel UID confirmé par relecture distante
        self.assertEqual(rl.liste(self.desk, ADMIN, {'source': 'brouillon'}, '/p')['lignes'][0]['id'], ident)   # identité conservée
        d2 = rl.detail(self.desk, ADMIN, ident, '/p')
        self.assertIn('merci', d2['corps'])
        with self.assertRaises(rl.Refus) as ctx:                                       # ancienne version : 409
            self.cmd(ADMIN, ident, 'enregistrer', revision=d['revision'], corps='Écrasement')
        self.assertEqual(ctx.exception.status, 409)
        self.assertEqual(self.cmd(ADMIN, ident, 'decision', action='ecarter', revision=d2['revision'])['etat'], 'ecarte')
        self.assertEqual(rl.liste(self.desk, ADMIN, {'source': 'brouillon'}, '/p')['lignes'], [])
        self.assertEqual(len(self.imap.folders['Drafts']), 1)                         # le brouillon reste dans la messagerie
        with self.assertRaises(rl.Refus) as ctx:
            self.cmd(ADMIN, ident, 'deposer', revision=d2['revision'])
        self.assertEqual(ctx.exception.status, 422)

    def test_P1_file5657_rend_l_identifiant_stable_du_brouillon(self):
        from agent import drafts440, file5657
        self.brouillon()
        listing = drafts440.list_drafts(self.f.c['mail'])
        self.desk.setting('cockpit530:drafts', {'at': time.time(), 'ok': True, 'validity': listing['uidvalidity'],
                                                'items': [{**{k: x.get(k, '') for k in ('uid', 'subject', 'to', 'date', 'agent_key', 'message_id', 'in_reply_to')},
                                                           'agent': True} for x in listing['items']]})
        cartes = [c for c in file5657.review_items(self.desk, '/p') if c['class'] == 'courriel']
        ident = rl.liste(self.desk, ADMIN, {'source': 'brouillon'}, '/p')['lignes'][0]['id']
        self.assertEqual([c['relecture'] for c in cartes], [ident])

    def test_P1_brouillon_sans_origine_prouvee_masque_au_compte_cloisonne(self):
        self.brouillon(repond_a='<origine-introuvable@example.test>')        # lien déclaré, non vérifiable sur le serveur
        self.assertEqual(len(rl.liste(self.desk, ADMIN, {'source': 'brouillon'}, '/p')['lignes']), 1)
        self.assertEqual(rl.liste(self.desk, AVOCAT_B, {'source': 'brouillon'}, '/p')['lignes'], [])

    def test_P1_retenir_un_brouillon_modifie_enregistre_puis_retient_sans_envoi(self):
        self.brouillon()
        ligne = rl.liste(self.desk, ADMIN, {'source': 'brouillon'}, '/p')['lignes'][0]
        d = rl.detail(self.desk, ADMIN, ligne['id'], '/p')
        c = 'cle-retenir-imap-0001'
        with patch('smtplib.SMTP', side_effect=AssertionError('aucun envoi')):
            recu = self.cmd(ADMIN, ligne['id'], 'decision', action='retenir', revision=d['revision'], corps='Cher Confrère,\n\nTexte relu.', cle=c)
            self.assertEqual(recu, self.cmd(ADMIN, ligne['id'], 'decision', action='retenir', revision=d['revision'],
                                            corps='Cher Confrère,\n\nTexte relu.', cle=c))   # rejeu : même reçu, aucun second enregistrement
        self.assertEqual(recu['etat'], 'retenu')
        self.assertEqual(rl.liste(self.desk, ADMIN, {'source': 'brouillon'}, '/p')['lignes'], [])
        textes = [v['raw'] for v in self.imap.folders['Drafts'].values() if '\\Deleted' not in v['flags']]
        self.assertEqual(len(textes), 1)
        self.assertIn(b'Texte relu', textes[0])

    def test_P1_panne_imap_ne_bloque_pas_la_liste_locale(self):
        self.proposition(1, sujet='Proposition locale')
        with patch('agent.drafts440.list_drafts', side_effect=OSError('connexion refusée')):
            imap = rl.liste(self.desk, ADMIN, {'source': 'brouillon'}, '/p')
            local = rl.liste(self.desk, ADMIN, {'source': 'tous'}, '/p')
        self.assertEqual((imap['lignes'], imap['sources']['imap']['etat'], imap['total_exact']), ([], 'indisponible', False))
        self.assertEqual(local['total'], 1)


# ---------------------------------------------------------------------------------------------------- lecteur : simulation Node
class Lecteur(unittest.TestCase):
    def node(self, racine):
        return subprocess.run([shutil.which('node'), str(RACINE / 'tests' / 'lecteur56100.cjs'), str(racine)],
                              capture_output=True, text=True, encoding='utf-8', timeout=60)

    @unittest.skipUnless(shutil.which('node'), 'Node.js indisponible')
    def test_R09_reponse_tardive_saisie_protegee_et_rejeu_idempotent_node(self):
        r = self.node(RACINE)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertRegex(r.stdout.strip().splitlines()[-1], r'^lecteur56100 : ok (\d+)/\1$')

    @unittest.skipUnless(shutil.which('node'), 'Node.js indisponible')
    def test_R09_mutation_sans_numero_de_selection_detectee(self):
        """Contre-épreuve sur une copie : sans le contrôle du numéro de sélection, la réponse tardive de A repeint le panneau."""
        source = (RACINE / 'agent/static/v56100-lecteur.js').read_text(encoding='utf-8')
        garde = "if (ticket !== seq || !data || data.id !== id) return false;"
        self.assertIn(garde, source)
        with tempfile.TemporaryDirectory() as tmp:
            statique = Path(tmp) / 'agent' / 'static'
            statique.mkdir(parents=True)
            (statique / 'v56100-lecteur.js').write_text(source.replace(garde, 'if (!data) return false;'), encoding='utf-8')
            r = self.node(tmp)
        self.assertNotEqual(r.returncode, 0, r.stdout)

    def test_R03_script_sans_html_recu_ni_script_en_ligne(self):
        js = (RACINE / 'agent/static/v56100-lecteur.js').read_text(encoding='utf-8')
        self.assertEqual(js.count('innerHTML'), 1)                                      # gabarit statique seulement
        self.assertIn('article.innerHTML = GABARIT;', js)
        self.assertIn('window.AX100' if 'window.AX100' in js else 'racine.AX100', js)
        self.assertIn('monter: monter', js)
        self.assertIn('AbortController', js)


if __name__ == '__main__':
    unittest.main()
