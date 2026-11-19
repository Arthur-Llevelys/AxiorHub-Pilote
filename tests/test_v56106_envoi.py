"""5.6.106 (lot E) : options d'envoi — annulation pendant quelques secondes, envoi programmé, accusé de lecture, importance haute.

Faux IMAP (``fake_imap440``), faux SMTP (``test_v490.FakeSMTP``), horloge simulée (paramètre ``now``). Aucun réseau.
Identifiants : E1 annulation et file d'envoi, E2 envoi programmé, E3 accusé de lecture, E4 importance, E5 zone et écrans,
G1-G6 garde-fous (personne seulement, effet unique, reprise prudente, plafond, pause, droits), R04/R05 routes et lectures."""
from datetime import datetime, timedelta
from email import policy
from email.parser import BytesParser
import io
import json
from pathlib import Path
import re
import threading
import time
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

from agent import envoi56106 as E, send490
from agent.common import Stop
from agent.desk import Desk
from fake_imap440 import FakeIMAP
from test_v440_drafts import raw_draft
from test_v490 import FakeSMTP, SendBase

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / 'agent' / 'static'
PARIS = ZoneInfo('Europe/Paris')
ORIGINE, JETON = 'https://pilote.example.test', 'jeton-csrf-e106'


class Base(SendBase):
    def setUp(self):
        super().setUp()
        self.enable()
        self.t0 = time.time()
        # 5.6.108 (lot C, B05) : le compte qui confirme est revérifié dans l'annuaire juste avant le départ ; « durand » est un compte
        # actif de la version serveur (annuaire fictif). Sans annuaire, un départ programmé longtemps après la confirmation serait retenu.
        from agent import connexion5648
        db = connexion5648.connect(self.f.c['state_dir'])
        db.execute("INSERT INTO users(ident,email,name,role,salt,hash,active,must_change,created) VALUES('durand','durand@example.test','DURAND',"
                   "'avocat','00','00',1,0,'2026-01-01T00:00:00+00:00')")
        db.commit()
        db.close()

    def file(self, quand='', accuse=False, importance=False, owner='durand', role='avocat'):
        p = send490.prepare(self.desk, self.uid, '7')
        now = time.time() + 2
        return E.mettre_en_file(self.desk, owner, role, p['token'], quand, accuse, importance, now=now), now

    def ligne(self, ident):
        return self.desk.db.execute('SELECT * FROM envoi_file56106 WHERE id=?', (ident,)).fetchone()

    def sent(self):
        return [x['message'] for x in FakeSMTP.sent]

    def in_drafts(self):
        return self.uid in FakeIMAP.folders['Drafts']


# ================================================================================ E1 : file d'envoi, « Annuler »
class AnnulerEnvoi(Base):
    def test_E1_01_confirmation_met_en_file_et_n_envoie_rien(self):
        with patch.object(send490, '_smtp_send', side_effect=AssertionError('aucun envoi pendant la confirmation')):
            out, now = self.file()
        self.assertTrue(out['file'])
        self.assertEqual((out['genre'], out['delai'], out['reste']), ('differe', 10, 10))
        row = self.ligne(out['id'])
        self.assertEqual(row['etat'], 'en_attente')
        self.assertAlmostEqual(row['depart'], now + 10, places=3)
        self.assertTrue(self.in_drafts(), 'le message reste dans Brouillons tant qu’il n’est pas parti')
        self.assertEqual(E.tick(self.desk, 'b1', now + 5), [])
        self.assertEqual(FakeSMTP.sent, [])
        self.assertEqual(E.tick(self.desk, 'b1', now + 10.5), [(out['id'], 'parti')])
        self.assertEqual(len(FakeSMTP.sent), 1)
        self.assertFalse(self.in_drafts())
        self.assertEqual(len(FakeIMAP.folders['Sent']), 1)
        self.assertEqual(self.ligne(out['id'])['etat'], 'parti')
        self.assertEqual([r[0] for r in self.desk.db.execute('SELECT status FROM send_log490')], ['envoye'])
        self.assertEqual(E.tick(self.desk, 'b1', now + 60), [])
        self.assertEqual(len(FakeSMTP.sent), 1)

    def test_E1_02_annuler_avant_le_depart_laisse_le_brouillon(self):
        out, now = self.file()
        r = E.annuler(self.desk, 'durand', out['id'], out['version'], now=now + 3)
        self.assertTrue(r['annule'])
        self.assertEqual((r['uid'], r['uidvalidity']), (str(self.uid), '7'))
        self.assertIn('Brouillons', r['message'])
        self.assertEqual(E.tick(self.desk, 'b1', now + 60), [])
        self.assertEqual(FakeSMTP.sent, [])
        self.assertTrue(self.in_drafts())
        self.assertTrue(E.annuler(self.desk, 'durand', out['id'], now=now + 4)['annule'])   # deuxième clic : même issue, aucun effet
        self.assertEqual(self.ligne(out['id'])['apercu'], '{}')

    def test_E1_03_annuler_trop_tard(self):
        out, now = self.file()
        E.tick(self.desk, 'b1', now + 11)
        with self.assertRaisesRegex(Stop, 'deja_parti'):
            E.annuler(self.desk, 'durand', out['id'], now=now + 12)

    def test_E1_04_delai_reglable_par_compte(self):
        self.assertEqual(E.delai(self.desk, 'durand'), 10)
        for v in (0, 5, 20, 30):
            self.assertEqual(E.regler_delai(self.desk, 'durand', v)['delai'], v)
        for bad in (7, -1, 'x', None, 60):
            with self.assertRaisesRegex(Stop, 'delai_invalide'):
                E.regler_delai(self.desk, 'durand', bad)
        self.assertEqual(E.delai(self.desk, 'martin'), 10)          # réglage personnel
        E.regler_delai(self.desk, 'durand', 0)
        out, now = self.file()
        self.assertEqual((out['reste'], out['delai']), (0, 0))
        self.assertEqual(E.tick(self.desk, 'b1', now), [(out['id'], 'parti')])

    def test_E1_05_depart_par_le_fil_du_travailleur(self):
        E.regler_delai(self.desk, 'durand', 0)
        out, _ = self.file()
        stop = threading.Event()
        fil = threading.Thread(target=E.lane, args=(self.f.c, stop, lambda c: Desk(c)), daemon=True)
        fil.start()
        try:
            limite = time.time() + 15
            while time.time() < limite and not FakeSMTP.sent:
                time.sleep(0.2)
        finally:
            stop.set()
            fil.join(5)
        self.assertEqual(len(FakeSMTP.sent), 1)
        self.desk.db.commit()
        self.assertEqual(self.ligne(out['id'])['etat'], 'parti')

    def test_E1_06_ancienne_route_de_la_messagerie_passe_aussi_par_la_file(self):
        from agent import web490
        p = send490.prepare(self.desk, self.uid, '7')
        time.sleep(1.05)
        with patch.object(send490, '_smtp_send', side_effect=AssertionError('jamais pendant la requête')):
            out = web490.webmail_confirm(self.desk, p['token'])
        self.assertTrue(out['file'])
        self.assertEqual(FakeSMTP.sent, [])

    def test_E1_07_meme_brouillon_deja_dans_la_file(self):
        out, now = self.file()
        p = send490.prepare(self.desk, self.uid, '7')
        with self.assertRaisesRegex(Stop, 'deja_en_file'):
            E.mettre_en_file(self.desk, 'durand', 'avocat', p['token'], now=time.time() + 2)
        self.assertEqual(send490.pending_fingerprint(self.desk, p['token'])[:6], self.ligne(out['id'])['fingerprint'][:6])   # jeton non consommé


# ================================================================================ E2 : envoi programmé
class EnvoiProgramme(Base):
    def quand(self, jours=3, heure=9):
        d = (datetime.now(PARIS) + timedelta(days=jours)).replace(hour=heure, minute=0, second=0, microsecond=0)
        return d.strftime('%Y-%m-%dT%H:%M'), d.timestamp()

    def test_E2_01_heure_du_cabinet_et_brouillon_conserve(self):
        texte, ts = self.quand()
        out, now = self.file(texte)
        self.assertEqual(out['genre'], 'programme')
        self.assertAlmostEqual(self.ligne(out['id'])['depart'], ts, places=3)
        self.assertIn(' à 09 h 00', out['depart_libelle'])
        self.assertTrue(out['depart'].startswith(texte))
        self.assertEqual(E.tick(self.desk, 'b1', ts - 1), [])
        self.assertTrue(self.in_drafts())
        listed = E.liste(self.desk, 'durand', now)['elements']
        self.assertEqual([x['id'] for x in listed], [out['id']])
        self.assertEqual(listed[0]['actions'], ['modifier', 'maintenant', 'annuler'])
        self.assertEqual(E.liste(self.desk, 'martin', now)['elements'], [])          # liste personnelle
        self.assertEqual(E.tick(self.desk, 'b1', ts + 1), [(out['id'], 'parti')])
        self.assertFalse(self.ligne(out['id'])['retard'])

    def test_E2_02_heure_passee_ou_illisible_refusee_sans_consommer_le_jeton(self):
        p = send490.prepare(self.desk, self.uid, '7')
        hier = (datetime.now(PARIS) - timedelta(days=1)).strftime('%Y-%m-%dT%H:%M')
        tout_de_suite = datetime.now(PARIS).strftime('%Y-%m-%dT%H:%M')
        loin = (datetime.now(PARIS) + timedelta(days=400)).strftime('%Y-%m-%dT%H:%M')
        for texte, code in ((hier, 'heure_d_envoi_passee'), (tout_de_suite, 'heure_d_envoi_passee'), ('demain 9h', 'date_envoi_invalide'),
                            ('2026-02-30T09:00', 'date_envoi_invalide'), (loin, 'date_envoi_trop_lointaine')):
            with self.assertRaisesRegex(Stop, code):
                E.mettre_en_file(self.desk, 'durand', 'avocat', p['token'], texte, now=time.time() + 2)
        texte, _ = self.quand()
        self.assertEqual(E.mettre_en_file(self.desk, 'durand', 'avocat', p['token'], texte, now=time.time() + 2)['genre'], 'programme')

    def test_E2_03_modifier_envoyer_maintenant_et_version(self):
        texte, ts = self.quand()
        out, now = self.file(texte)
        autre, ts2 = self.quand(jours=5, heure=14)
        r = E.modifier(self.desk, 'durand', out['id'], autre, accuse=True, importance=True, version=out['version'], now=now)
        self.assertAlmostEqual(self.ligne(out['id'])['depart'], ts2, places=3)
        with self.assertRaisesRegex(Stop, 'version_en_conflit'):
            E.modifier(self.desk, 'durand', out['id'], texte, version=out['version'], now=now)
        with self.assertRaisesRegex(Stop, 'heure_d_envoi_passee'):
            E.modifier(self.desk, 'durand', out['id'], datetime.now(PARIS).strftime('%Y-%m-%dT%H:%M'), version=r['version'], now=now)
        with self.assertRaisesRegex(Stop, 'introuvable'):
            E.maintenant(self.desk, 'martin', out['id'], now=now)                     # envoi d'un autre compte : 404 neutre
        E.maintenant(self.desk, 'durand', out['id'], r['version'], now=now + 5)
        self.assertEqual(E.tick(self.desk, 'b1', now + 6), [(out['id'], 'parti')])
        msg = FakeSMTP.sent[0]['message']
        self.assertEqual((msg['Importance'], msg['X-Priority']), ('high', '1'))
        self.assertTrue(msg['Disposition-Notification-To'])

    def test_E2_04_serveur_arrete_a_l_heure_prevue_envoye_en_retard(self):
        texte, ts = self.quand()
        out, now = self.file(texte)
        self.assertEqual(E.tick(self.desk, 'redemarrage', ts + 3600), [(out['id'], 'parti')])
        row = self.ligne(out['id'])
        self.assertEqual((row['etat'], row['retard']), ('parti', 1))
        item = E.liste(self.desk, 'durand', ts + 3700)['elements'][0]
        self.assertTrue(item['retard'])
        self.assertTrue(item['objet'])
        self.assertEqual(item['actions'], ['retirer'])
        E.retirer(self.desk, 'durand', out['id'], now=ts + 3800)
        self.assertEqual(E.liste(self.desk, 'durand', ts + 3900)['elements'], [])


# ================================================================================ E3 / E4 : en-têtes d'options
class EnTetes(Base):
    def go(self, **kw):
        E.regler_delai(self.desk, 'durand', 0)
        out, now = self.file(**kw)
        E.tick(self.desk, 'b1', now)
        return FakeSMTP.sent[-1]['message']

    def test_E3_accuse_de_lecture_rfc8098(self):
        msg = self.go(accuse=True)
        self.assertEqual(msg.get_all('Disposition-Notification-To'), ['<cabinet@example.test>'])
        self.assertIsNone(msg['Importance'])

    def test_E4_importance_haute(self):
        msg = self.go(importance=True)
        self.assertEqual((msg.get_all('Importance'), msg.get_all('X-Priority')), (['high'], ['1']))
        self.assertIsNone(msg['Disposition-Notification-To'])
        saved = BytesParser(policy=policy.default).parsebytes(list(FakeIMAP.folders['Sent'].values())[0]['raw'])
        self.assertEqual(saved['Importance'], 'high')                          # copie identique dans Envoyés

    def test_E3_E4_sans_option_aucun_en_tete_et_jamais_double(self):
        msg = self.go()
        for name in ('Importance', 'X-Priority', 'Disposition-Notification-To'):
            self.assertIsNone(msg[name], name)
        raw = raw_draft().replace(b'Subject:', b'Importance: low\r\nSubject:', 1)
        out = send490._outgoing(BytesParser(policy=policy.default).parsebytes(raw), self.f.c['mail'], extra_headers=E.en_tetes({'importance': True}, ''))
        self.assertEqual(out.get_all('Importance'), ['high'])
        with self.assertRaisesRegex(Stop, 'en_tete_invalide'):
            send490._outgoing(BytesParser(policy=policy.default).parsebytes(raw_draft()), self.f.c['mail'],
                              extra_headers=[('Disposition-Notification-To', '<a@example.test>\r\nBcc: x@example.test')])


# ================================================================================ garde-fous
class GardeFous(Base):
    def test_G1_seule_une_personne_met_un_message_dans_la_file(self):
        p = send490.prepare(self.desk, self.uid, '7')
        for attr, value in (('active_job_id', 42), ('origin_context', 'automatic')):
            setattr(self.desk, attr, value)
            try:
                with self.assertRaisesRegex(Stop, 'envoi_reserve_a_une_personne'):
                    E.mettre_en_file(self.desk, 'durand', 'avocat', p['token'], now=time.time() + 2)
            finally:
                delattr(self.desk, attr)
        with patch('agent.membres5650.current_author', return_value='jeton_service'):
            with self.assertRaisesRegex(Stop, 'envoi_reserve_a_une_personne'):
                E.mettre_en_file(self.desk, 'durand', 'avocat', p['token'], now=time.time() + 2)
        self.assertTrue(send490.pending_fingerprint(self.desk, p['token']))    # refusé avant de consommer le jeton
        self.desk.active_job_id = 42                                         # mutation : garde-fou retiré → l'automatisme passerait
        try:
            with patch.object(E, '_humain_requis', lambda desk: None):
                self.assertTrue(E.mettre_en_file(self.desk, 'durand', 'avocat', p['token'], now=time.time() + 2)['file'])
        finally:
            del self.desk.active_job_id

    def test_G1_depart_reserve_aux_messages_confirmes_et_reserves(self):
        from agent import web490
        with self.assertRaisesRegex(Stop, 'envoi_non_confirme'):
            web490.file_depart(self.desk, str(self.uid), '7', 'f' * 64, 'DEMO', '<inconnu@example.test>')
        out, now = self.file()
        row = self.ligne(out['id'])                                          # confirmé mais pas encore réservé par le fil d'envoi
        with self.assertRaisesRegex(Stop, 'envoi_non_confirme'):
            web490.file_depart(self.desk, row['uid'], row['validity'], row['fingerprint'], row['matter'], '<x@example.test>')
        self.assertEqual(FakeSMTP.sent, [])

    def test_G1_aucun_autre_module_n_ecrit_dans_la_file(self):
        writers = []
        for path in (ROOT / 'agent').rglob('*.py'):
            text = path.read_text(encoding='utf-8', errors='replace')
            if re.search(r'INSERT\s+INTO\s+envoi_file56106|envoi56106\.mettre_en_file|envoi56106 import[^\n]*mettre_en_file', text) and \
                    path.name not in ('envoi56106.py', 'web490.py'):
                writers.append(path.name)
        self.assertEqual(writers, [])
        self.assertNotIn('mettre_en_file', (ROOT / 'agent' / 'ia540.py').read_text(encoding='utf-8'))

    def test_G2_effet_unique_deux_passes_croisees(self):
        self.assertEqual(self._mutation_croisee(), 1)              # l'autre passe part pendant que celle-ci hésite : un seul départ
        self.desk.db.commit()
        self.assertEqual([r[0] for r in self.desk.db.execute('SELECT etat FROM envoi_file56106')], ['parti'])

    def _mutation_croisee(self, reserver=None, doublons=None):
        out, now = self.file()
        self._dans = False

        def pass_imbriquee(desk, real=E._pause):
            real(desk)
            if not self._dans:
                self._dans = True
                E.tick(Desk(self.f.c), 'autre-processus', now + 11)
        # la messagerie n'a pas retiré le brouillon après le premier départ (incident toléré par le départ) : seuls la réservation et le
        # journal des envois empêchent alors un second départ
        patches = [patch.object(E, '_pause', pass_imbriquee), patch('agent.drafts440.discard_draft', side_effect=OSError('messagerie'))]
        if reserver:
            patches.append(patch.object(E, '_reserver', reserver))
        if doublons:
            patches.append(patch.object(send490, '_duplicate_check', doublons))
        for p in patches:
            p.start()
        try:
            E.tick(self.desk, 'b1', now + 11)
        finally:
            for p in patches:
                p.stop()
        return len(FakeSMTP.sent)

    def test_G2_mutation_sans_reservation_ni_controle_des_doublons_double_envoi(self):
        def reserver_naif(desk, row, bail, now):            # mutation : la réservation ne vérifie plus l'état ni la version
            desk.db.execute("UPDATE envoi_file56106 SET etat='en_cours', etape='reserve', bail=?, message_id=? WHERE id=?",
                            (bail, '<m-%s@example.test>' % bail, row['id']))
            desk.db.commit()
            return True
        self.assertEqual(self._mutation_croisee(reserver=reserver_naif, doublons=lambda desk, fp: None), 2)

    def test_G2_chaque_garde_fou_suffit_seul(self):
        def reserver_naif(desk, row, bail, now):
            desk.db.execute("UPDATE envoi_file56106 SET etat='en_cours', etape='reserve', bail=?, message_id=? WHERE id=?",
                            (bail, '<m-%s@example.test>' % bail, row['id']))
            desk.db.commit()
            return True
        self.assertEqual(self._mutation_croisee(doublons=lambda desk, fp: None), 1)     # réservation seule
        FakeSMTP.sent.clear()
        FakeIMAP.reset()
        self.uid = FakeIMAP.add('Drafts', raw_draft(), ['\\Draft'])
        self.desk.db.execute('DELETE FROM send_log490')
        self.desk.db.execute('DELETE FROM envoi_file56106')
        self.desk.db.commit()
        self.assertEqual(self._mutation_croisee(reserver=reserver_naif), 1)              # journal des envois seul

    # ---------------------------------------------------------------- G3 : redémarrage en plein envoi
    def _crash_apres_smtp(self):
        out, now = self.file()
        real = send490._smtp_send

        def smtp_puis_arret(*a, **k):
            real(*a, **k)
            raise SystemExit('arrêt du serveur juste après le SMTP')
        with patch.object(send490, '_smtp_send', smtp_puis_arret):
            with self.assertRaises(SystemExit):
                E.tick(self.desk, 'processus-mort', now + 11)
        self.desk.db.rollback()
        row = self.ligne(out['id'])
        self.assertEqual((row['etat'], row['etape']), ('en_cours', 'smtp'))
        return out, now, row

    def test_G3_reprise_smtp_jamais_commence_repart(self):
        out, now = self.file()
        row = self.ligne(out['id'])
        self.assertTrue(E._reserver(self.desk, row, 'processus-mort', now + 11))        # arrêt juste après la réservation
        self.assertEqual(E.tick(self.desk, 'nouveau', now + 12), [(out['id'], 'parti')])
        self.assertEqual(len(FakeSMTP.sent), 1)

    def test_G3_reprise_message_retrouve_dans_envoyes(self):
        out, now, row = self._crash_apres_smtp()
        FakeIMAP.add('Sent', FakeSMTP.sent[0]['message'].as_bytes(policy=policy.SMTP), ['\\Seen'])   # la copie avait eu lieu
        E.tick(self.desk, 'nouveau', now + 20)
        self.assertEqual(self.ligne(out['id'])['etat'], 'parti')
        self.assertEqual(len(FakeSMTP.sent), 1)
        self.assertEqual(self.desk.db.execute('SELECT status FROM send_log490 WHERE id=?', (row['log_id'],)).fetchone()[0], 'envoye')
        self.assertFalse(self.in_drafts())

    def test_G3_reprise_dans_le_doute_signale_et_ne_renvoie_jamais(self):
        out, now, row = self._crash_apres_smtp()
        for k in range(5):
            E.tick(self.desk, 'nouveau', now + 20 + k * 60)
        self.assertEqual(len(FakeSMTP.sent), 1)
        row = self.ligne(out['id'])
        self.assertEqual((row['etat'], row['motif']), ('a_verifier', 'envoi_interrompu_a_verifier'))
        item = E.liste(self.desk, 'durand', now + 400)['elements'][0]
        self.assertIn('vérifiez', item['motif_libelle'])
        self.assertEqual(item['actions'], ['retirer'])
        with self.assertRaisesRegex(Stop, 'action_impossible'):
            E.maintenant(self.desk, 'durand', out['id'], now=now + 500)
        self.assertTrue(self.in_drafts())

    def test_G3_mutation_reprise_naive_double_envoi(self):
        out, now, row = self._crash_apres_smtp()

        def reprise_naive(desk, bail, now=None):              # mutation : tout « en cours » repart, sans regarder Envoyés
            desk.db.execute("UPDATE envoi_file56106 SET etat='en_attente', etape='', bail='', version=version+1 WHERE etat='en_cours'")
            desk.db.commit()
        with patch.object(E, 'reprendre', reprise_naive), patch.object(send490, '_duplicate_check', lambda desk, fp: None):
            E.tick(self.desk, 'nouveau', now + 20)
        self.assertEqual(len(FakeSMTP.sent), 2)

    def test_G3_messagerie_injoignable_rien_n_est_decide(self):
        out, now, row = self._crash_apres_smtp()
        with patch.object(E, '_dans_envoyes', return_value=None):
            E.tick(self.desk, 'nouveau', now + 20)
        self.assertEqual(self.ligne(out['id'])['etat'], 'en_cours')
        self.assertEqual(len(FakeSMTP.sent), 1)

    # ---------------------------------------------------------------- G4 : plafond, pause
    def test_G4_plafond_horaire_compte_au_depart_reel(self):
        out, now = self.file()
        stamp = send490.now_iso()
        for _ in range(send490.MAX_PER_HOUR):
            self.desk.db.execute("INSERT INTO send_log490(at,matter,fingerprint,recipients,status) VALUES(?,?,?,?,'envoye')", (stamp, '', 'x', 1))
        self.desk.db.commit()
        self.assertEqual(E.tick(self.desk, 'b1', now + 11), [(out['id'], 'en_attente')])
        row = self.ligne(out['id'])
        self.assertEqual((row['etat'], row['motif'], FakeSMTP.sent), ('en_attente', 'plafond_horaire_envois', []))
        self.assertEqual(E.tick(self.desk, 'b1', now + 12), [])                       # nouvel essai plus tard, pas à chaque seconde
        self.desk.db.execute("DELETE FROM send_log490 WHERE fingerprint='x'")
        self.desk.db.commit()
        self.assertEqual(E.tick(self.desk, 'b1', now + 11 + E.ATTENTE + 1), [(out['id'], 'parti')])
        self.assertEqual(self.ligne(out['id'])['motif'], '')

    def test_G5_pause_generale_bloque_le_depart_et_le_signale(self):
        # 5.6.108 (lot P), demande du cabinet du 18/11/2026 : la pause de l'IA ne retient plus un envoi que l'avocat a validé ; la suspension
        # des envois programmés (réglage distinct) retient le départ programmé et le signale ; à la reprise, rien ne part sans décision
        # (détail : test_v56108_pause).
        texte = (datetime.now(PARIS) + timedelta(days=3)).replace(hour=9, minute=0, second=0, microsecond=0).strftime('%Y-%m-%dT%H:%M')
        out, now = self.file(texte)
        depart = self.ligne(out['id'])['depart']
        self.desk.setting('missions567:pause', True)
        E.suspendre(self.desk, 'durand', True, now=now)
        self.assertEqual(E.tick(self.desk, 'b1', depart + 1), [(out['id'], 'suspendu')])
        self.assertEqual(FakeSMTP.sent, [])
        item = E.etat(self.desk, 'durand', out['id'], depart + 2)
        self.assertEqual(item['motif'], 'envois_suspendus')
        self.assertIn('suspendus', item['motif_libelle'])
        E.suspendre(self.desk, 'durand', False, now=depart + 3600)
        self.assertEqual(E.tick(self.desk, 'b1', depart + 3601), [])               # reprise : aucun départ sans décision
        self.assertEqual(FakeSMTP.sent, [])
        r = E.maintenant(self.desk, 'durand', out['id'], now=depart + 3602)        # décision explicite, l'IA toujours en pause
        self.assertEqual(r['version'], self.ligne(out['id'])['version'])
        self.assertEqual(E.tick(self.desk, 'b1', depart + 3603), [(out['id'], 'parti')])
        self.assertEqual(self.ligne(out['id'])['prevu'], depart)                   # l'heure prévue d'origine reste affichée

    def test_G5_point_de_controle_commun_appele_avant_l_effet(self):
        out, now = self.file()
        appels = []

        def checkpoint(desk):
            appels.append(1)
            raise Stop('automatismes_en_pause')
        with patch('agent.pause5629.checkpoint', checkpoint):
            E.tick(self.desk, 'b1', now + 11)
        self.assertTrue(appels)
        self.assertEqual(FakeSMTP.sent, [])
        self.assertEqual(self.ligne(out['id'])['etape'], '')

    def test_G5_pause_posee_entre_la_reservation_et_le_smtp(self):
        out, now = self.file()
        calls = {'n': 0}
        real = E._pause

        def second_appel(desk):
            calls['n'] += 1
            if calls['n'] == 2:
                raise Stop('automatismes_en_pause')
            return real(desk)
        with patch.object(E, '_pause', second_appel):
            self.assertEqual(E.tick(self.desk, 'b1', now + 11), [(out['id'], 'en_attente')])
        self.assertEqual(FakeSMTP.sent, [])
        self.assertEqual(self.desk.db.execute('SELECT COUNT(*) FROM send_log490').fetchone()[0], 0)

    # ---------------------------------------------------------------- G6 : brouillon et droits au départ
    def test_G6_brouillon_modifie_apres_la_confirmation(self):
        out, now = self.file()
        FakeIMAP.folders['Drafts'][self.uid]['raw'] = raw_draft().replace(b'Bien re', b'Autre texte re')
        self.assertEqual(E.tick(self.desk, 'b1', now + 11), [(out['id'], 'echec')])
        self.assertEqual(self.ligne(out['id'])['motif'], 'brouillon_modifie_depuis_confirmation')
        self.assertEqual(FakeSMTP.sent, [])

    def test_G6_brouillon_retire_apres_la_confirmation(self):
        out, now = self.file()
        del FakeIMAP.folders['Drafts'][self.uid]
        self.assertEqual(E.tick(self.desk, 'b1', now + 11), [(out['id'], 'echec')])
        self.assertEqual(self.ligne(out['id'])['motif'], 'brouillon_modifie_depuis_confirmation')

    def test_G6_droit_envoyer_retire_avant_le_depart(self):
        out, now = self.file()
        with patch('agent.membres5650.allowed', return_value=False):
            self.assertEqual(E.tick(self.desk, 'b1', now + 11), [(out['id'], 'echec')])
        self.assertEqual(self.ligne(out['id'])['motif'], 'droit_envoi_retire')
        self.assertEqual(FakeSMTP.sent, [])

    def test_G6_envoi_desactive_avant_le_depart(self):
        out, now = self.file()
        send490.set_enabled(self.desk, False)
        self.assertEqual(E.tick(self.desk, 'b1', now + 11), [(out['id'], 'echec')])
        self.assertEqual(self.ligne(out['id'])['motif'], 'envoi_desactive')

    def test_G6_echec_smtp_franc_puis_reessayer(self):
        out, now = self.file()
        FakeSMTP.fail = True
        self.assertEqual(E.tick(self.desk, 'b1', now + 11), [(out['id'], 'echec')])
        self.assertTrue(self.in_drafts())
        FakeSMTP.fail = False
        E.maintenant(self.desk, 'durand', out['id'], now=now + 20)
        self.assertEqual(E.tick(self.desk, 'b1', now + 21), [(out['id'], 'parti')])
        self.assertEqual(len(FakeSMTP.sent), 1)


# ================================================================================ routes, droits, lectures
class Routes(Base):
    def appel(self, route, corps=None, owner='durand', role='avocat', method='POST', args=None):
        env = {'HTTP_X_AXIORHUB_USER': owner, 'HTTP_X_AXIORHUB_ROLE': role}
        if method == 'POST':
            brut = json.dumps(corps or {}).encode()
            env.update({'HTTP_ORIGIN': ORIGINE, 'HTTP_X_CSRF_TOKEN': JETON, 'CONTENT_TYPE': 'application/json',
                        'CONTENT_LENGTH': str(len(brut)), 'wsgi.input': io.BytesIO(brut)})
        out = E.api(env, self.desk, {'origin': ORIGINE, 'csrf': JETON}, '/p', E.PREFIXE + route, args or {}, method)
        return out['status'], json.loads(out['body']), out

    def confirmer(self, **kw):
        p = send490.prepare(self.desk, self.uid, '7')
        time.sleep(1.05)
        return self.appel('confirmer', {'token': p['token'], **kw})

    def test_R04_roles_et_droits(self):
        from agent import membres5650
        from agent.standalone_auth import allowed
        for route in E.ROUTES_POST:
            self.assertTrue(allowed('avocat', 'POST', '/api440/' + E.PREFIXE + route))
            self.assertFalse(allowed('assistant', 'POST', '/api440/' + E.PREFIXE + route))
        self.assertFalse(allowed('avocat', 'POST', '/api440/' + E.PREFIXE + 'inconnue'))
        self.assertEqual(membres5650.category('POST', '/api440/' + E.PREFIXE + 'confirmer'), 'envoyer')
        self.assertEqual(membres5650.category('POST', '/api440/' + E.PREFIXE + 'annuler'), 'envoyer')
        self.assertIsNone(membres5650.category('POST', '/api440/' + E.PREFIXE + 'reglage'))
        status, body, _ = self.appel('liste', owner='x', role='assistant', method='GET')
        self.assertEqual((status, body['error']), ('403 Forbidden', 'role_insuffisant'))

    def test_R04_confirmer_par_la_route_puis_annuler(self):
        status, body, raw = self.confirmer(importance=True, cle='cle-confirmer-0001')
        self.assertEqual(status, '200 OK', body)
        self.assertIn(('Cache-Control', 'private, no-store'), raw['headers'])
        self.assertEqual(FakeSMTP.sent, [])
        again = self.appel('confirmer', {'token': 'ignore', 'cle': 'cle-confirmer-0001'})
        self.assertEqual(again[0], '409 Conflict')                                       # autre usage de la clé
        status, annule, _ = self.appel('annuler', {'id': body['id'], 'version': body['version'], 'cle': 'cle-annuler-0001'})
        self.assertEqual((status, annule['annule']), ('200 OK', True))
        status, bis, _ = self.appel('annuler', {'id': body['id'], 'version': body['version'], 'cle': 'cle-annuler-0001'})
        self.assertEqual(bis, annule)                                                     # même clé : même reçu

    def test_R04_meme_404_neutre_absent_ou_autre_compte(self):
        status, body, _ = self.confirmer()
        absent = self.appel('annuler', {'id': 'inexistant'})
        autre = self.appel('annuler', {'id': body['id']}, owner='martin')
        self.assertEqual(absent[0], '404 Not Found')
        self.assertEqual((absent[0], absent[1]), (autre[0], autre[1]))
        self.assertEqual(self.appel('etat', owner='martin', method='GET', args={'id': body['id']})[:2], absent[:2])
        stale = self.appel('maintenant', {'id': body['id'], 'version': 99})
        self.assertEqual((stale[0], stale[1]['error']), ('409 Conflict', 'version_en_conflit'))

    def test_R04_garde_d_envoi_appelee_a_la_confirmation(self):
        p = send490.prepare(self.desk, self.uid, '7')
        time.sleep(1.05)
        with patch('agent.acces_courriel56100.garde_envoi', side_effect=Stop('introuvable')) as garde:
            status, body, _ = self.appel('confirmer', {'token': p['token']})
        self.assertTrue(garde.called)
        self.assertEqual(status, '404 Not Found')
        self.assertTrue(send490.pending_fingerprint(self.desk, p['token']))               # rien de consommé, rien en file
        self.assertEqual(self.desk.db.execute('SELECT COUNT(*) FROM envoi_file56106').fetchone()[0], 0)

    def test_R05_lectures_sans_ecriture(self):
        status, body, _ = self.confirmer()
        before = self.desk.db.total_changes
        for _ in range(3):
            self.assertEqual(self.appel('liste', method='GET')[0], '200 OK')
            self.assertEqual(self.appel('etat', method='GET', args={'id': body['id']})[0], '200 OK')
        self.assertEqual(self.desk.db.total_changes, before)
        liste = self.appel('liste', method='GET')[1]
        self.assertEqual((liste['delai'], liste['delais']), (10, [0, 5, 10, 20, 30]))
        self.assertNotIn('fingerprint', json.dumps(liste))

    def test_R04_reglage_et_route_inconnue(self):
        self.assertEqual(self.appel('reglage', {'delai': 20})[1]['delai'], 20)
        self.assertEqual(self.appel('reglage', {'delai': 7})[0], '400 Bad Request')
        self.assertEqual(self.appel('detruire', {'id': 'x'})[0], '404 Not Found')


# ================================================================================ E5 : zone, écrans, montage
class Ecrans(unittest.TestCase):
    def test_E5_bloc_d_envoi_et_zone_d_options(self):
        js = (STATIC / 'v5628.js').read_text(encoding='utf-8')
        for hook in ('axh56106Envoi', 'bx5628-options-envoi', 'm5628/envoyer/confirmer', 'resume(form)', 'reprendre'):
            self.assertIn(hook, js)
        e = (STATIC / 'v56106-envoi.js').read_text(encoding='utf-8')
        for hook in ('m56106/envoi/confirmer', 'm56106/envoi/annuler', "agir('maintenant'", "agir('modifier'", "agir('annuler'", 'm56106/envoi/liste',
                     'Importance haute', 'Demander un accusé de lecture', 'Programmer l’envoi', 'Envois programmés', 'Annuler', 'datetime-local',
                     'aria-live', 'MutationObserver', 'cle()'):
            self.assertIn(hook, e)
        self.assertNotIn('innerHTML', e)
        self.assertNotRegex(e, r'smtp|eval\(')
        css = (STATIC / 'v56106-envoi.css').read_text(encoding='utf-8')
        self.assertIn('prefers-color-scheme: dark', css)
        self.assertIn(':root[data-theme="dark"]', css)
        self.assertIn('focus-visible', css)

    def test_E5_montage_statiques_et_feuille_unique(self):
        from agent.shell501 import BUNDLE
        from agent.web440 import STATIC as ST
        self.assertIn('v56106-envoi.css', BUNDLE)
        self.assertEqual(ST['/static/v56106-envoi.js'], 'text/javascript')
        self.assertIn('v56106-envoi.css', (STATIC / 'app520.css').read_text(encoding='utf-8'))
        helpers = (ROOT / 'agent' / 'ui_helpers.py').read_text(encoding='utf-8')
        self.assertIn('<script defer src="\'+escape(prefix,quote=True)+\'/static/v56106-envoi.js"></script>', helpers)
        desk = (ROOT / 'agent' / 'desk.py').read_text(encoding='utf-8')
        self.assertIn('start_send_lane(c,stop)', desk)
        self.assertIn('ensure_envoi56106_schema(self)', desk)


if __name__ == '__main__':
    unittest.main()
