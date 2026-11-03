"""5.6.89 (cahier § 26 à 30) : pauses programmées de l'IA, scénarios HO01 à HO24 avec une horloge simulée.

Aucun modèle réel, aucun appel réseau : les transports sont remplacés par des faux services. Les essais contrôlent la logique du serveur. La présentation
dans un vrai navigateur (HO22) et l'interruption chez un fournisseur réel (HO14) n'y sont pas prouvées."""
import base64
from datetime import date, datetime, timedelta, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import threading
import time
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

import test_v5652 as base
import test_v5682 as routeur
from agent import ai_gateway, arret5643, export5688, guide5631, horaires_ia5689 as h, horaires_web5689 as hw, membres5650, model as modele, notes5666, operations, pause5629, profil5634, queue521
from agent import rag, relevance370, routage5682, routines5652 as rt, routines520, standalone_auth, watch430
from agent.arret5643 import pause_all
from agent.common import HTTP, Stop
from agent.desk import Desk
from agent.index import DocumentIndex
from agent.model import Model
import test_agent as fixtures

MID = base.MID
PARIS = ZoneInfo('Europe/Paris')
TOUS = [1, 2, 3, 4, 5, 6, 7]
NUIT = [{'id': 'soir', 'days': TOUS, 'kind': 'period', 'start': '18:00', 'end': '00:00'}, {'id': 'nuit', 'days': TOUS, 'kind': 'period', 'start': '00:00', 'end': '07:00'}]
ROOT = Path(__file__).resolve().parents[1]


def paris(*args, fold=0):
    return datetime(*args, tzinfo=PARIS, fold=fold).astimezone(timezone.utc)


LUNDI = paris(2026, 11, 9, 12)


class Horloge:
    def __init__(self, instant=LUNDI):
        self.instant = instant

    def __call__(self):
        return self.instant


def politique(fenetres, actif=True, fuseau='Europe/Paris', revision=0):
    return {'schema_version': 1, 'revision': revision, 'enabled': actif, 'timezone': fuseau, 'scope': 'cabinet', 'windows': fenetres}


def calcule(fenetres, instant, fuseau='Europe/Paris'):
    return h.lire_horaire({h.CLE_POLITIQUE: json.dumps(politique(fenetres, fuseau=fuseau))}, instant)


class Base(base.Base):
    def setUp(self):
        super().setUp()
        self.horloge = Horloge()
        p = patch.object(h, 'maintenant', self.horloge)
        p.start()
        self.addCleanup(p.stop)

    def activer(self, fenetres=NUIT, actif=True, fuseau='Europe/Paris'):
        revision = h.etat_public(self.desk)['revision']
        return h.enregistrer(self.desk, {'revision': revision, 'enabled': actif, 'timezone': fuseau, 'windows': fenetres}, 'admin@example.test', confirmer=True)

    def a(self, *args, **kw):
        self.horloge.instant = paris(*args, **kw)
        return self.horloge.instant

    def statut(self, jid, desk=None):
        return (desk or self.desk).db.execute('SELECT status,stop_reason,attempts FROM jobs WHERE id=?', (jid,)).fetchone()

    def transitions(self):
        return [json.loads(r[0]) for r in self.desk.db.execute("SELECT data FROM audit WHERE action='horaires_ia5689_transition' ORDER BY id")]

    def suspendre(self, kind='refresh_brief', args=None, origin='automatic', created=None, debut=None):
        """Travail suspendu par le planning, comme à l'entrée en pause."""
        args = {'matter': MID} if args is None else args
        with patch.object(pause5629, 'check_enqueue'):
            jid = self.desk.enqueue(kind, args, origin=origin)
        self.desk.db.execute("UPDATE jobs SET status='suspended',stop_reason='pause_horaire',created=COALESCE(?,created),pause_debut=? WHERE id=?",
                             (created, (debut or self.horloge.instant).isoformat(), jid))
        self.desk.db.commit()
        return jid


# ---------------------------------------------------------------- § 29 : horaires et priorités
class Calcul(unittest.TestCase):
    def test_HO01_frontieres_exactes(self):
        self.assertFalse(calcule(NUIT, paris(2026, 11, 9, 17, 59, 59))['pause'])
        self.assertTrue(calcule(NUIT, paris(2026, 11, 9, 18))['pause'])          # début inclus
        self.assertTrue(calcule(NUIT, paris(2026, 11, 10, 6, 59, 59))['pause'])
        self.assertFalse(calcule(NUIT, paris(2026, 11, 10, 7))['pause'])         # fin exclue

    def test_HO02_deux_plages_jointives_forment_une_seule_pause(self):
        fusion, _ = h.plages_utc(politique(NUIT), date(2026, 11, 9), date(2026, 11, 9))
        self.assertEqual(fusion[0], (paris(2026, 11, 9, 0), paris(2026, 11, 9, 7)))
        self.assertEqual(fusion[1][0], paris(2026, 11, 9, 18))
        minuit = calcule(NUIT, paris(2026, 11, 10, 0))
        self.assertTrue(minuit['pause'])
        self.assertEqual(calcule(NUIT, paris(2026, 11, 9, 23, 59, 59))['fin'], paris(2026, 11, 10, 7))   # pas de reprise annoncée à minuit
        self.assertEqual(minuit['fin'], paris(2026, 11, 10, 7))

    def test_HO03_nuit_attachee_au_jour_de_debut(self):
        vendredi = [{'id': 'v', 'days': [5], 'kind': 'period', 'start': '18:00', 'end': '07:00'}]
        self.assertTrue(calcule(vendredi, paris(2026, 11, 13, 18))['pause'])
        self.assertTrue(calcule(vendredi, paris(2026, 11, 14, 6, 59))['pause'])  # samedi matin, samedi non coché
        self.assertFalse(calcule(vendredi, paris(2026, 11, 14, 7))['pause'])
        self.assertFalse(calcule(vendredi, paris(2026, 11, 14, 18))['pause'])
        self.assertFalse(calcule(vendredi, paris(2026, 11, 13, 6))['pause'])     # vendredi matin : la nuit de jeudi n'est pas cochée
        dimanche = [{'id': 'd', 'days': [7], 'kind': 'period', 'start': '20:00', 'end': '02:00'}]
        self.assertTrue(calcule(dimanche, paris(2026, 11, 16, 1, 59))['pause'])  # lundi matin : la semaine ISO change, pas la règle
        self.assertFalse(calcule(dimanche, paris(2026, 11, 16, 20))['pause'])

    def test_HO04_union_et_journee_entiere(self):
        chevauchement = [{'id': 'a', 'days': [1], 'kind': 'period', 'start': '08:00', 'end': '12:00'}, {'id': 'b', 'days': [1], 'kind': 'period', 'start': '10:00', 'end': '14:00'},
                         {'id': 'c', 'days': [1], 'kind': 'period', 'start': '09:00', 'end': '09:30'}]
        fusion, _ = h.plages_utc(politique(chevauchement), date(2026, 11, 9), date(2026, 11, 9))
        self.assertEqual(fusion, [(paris(2026, 11, 9, 8), paris(2026, 11, 9, 14))])
        self.assertEqual(calcule(chevauchement, paris(2026, 11, 9, 11))['fin'], paris(2026, 11, 9, 14))
        for debut, fin in (('18:00', '18:00'), ('00:00', '24:00'), ('00:00', '00:00')):
            with self.assertRaisesRegex(Stop, 'horaires_debut_egal_fin'):
                h.valider(politique([{'id': 'x', 'days': [1], 'kind': 'period', 'start': debut, 'end': fin}]))
        entiere = [{'id': 'f', 'days': [2], 'kind': 'full_day'}]
        self.assertFalse(calcule(entiere, paris(2026, 11, 9, 23, 59, 59))['pause'])
        self.assertTrue(calcule(entiere, paris(2026, 11, 10, 0))['pause'])
        self.assertTrue(calcule(entiere, paris(2026, 11, 10, 23, 59, 59))['pause'])
        self.assertFalse(calcule(entiere, paris(2026, 11, 11, 0))['pause'])
        normale = h.valider(politique([{'id': 'x', 'days': [1], 'kind': 'period', 'start': '18:00', 'end': '24:00'}]))
        self.assertEqual(normale['windows'][0]['end'], '00:00')                  # 24:00 : minuit suivant

    def test_HO05_heure_avancee_au_printemps(self):
        saut = paris(2027, 3, 28, 1, 59, 59) + timedelta(seconds=1)              # premier instant valide : 3 h, heure d'été
        self.assertEqual(saut.astimezone(PARIS).hour, 3)
        fin_dans_le_saut = [{'id': 'a', 'days': [6], 'kind': 'period', 'start': '20:00', 'end': '02:30'}]
        self.assertEqual(calcule(fin_dans_le_saut, paris(2027, 3, 27, 21))['fin'], saut)
        debut_dans_le_saut = [{'id': 'b', 'days': [7], 'kind': 'period', 'start': '02:30', 'end': '05:00'}]
        self.assertFalse(calcule(debut_dans_le_saut, saut - timedelta(seconds=1))['pause'])
        self.assertTrue(calcule(debut_dans_le_saut, saut)['pause'])
        vide = [{'id': 'c', 'days': [7], 'kind': 'period', 'start': '02:10', 'end': '02:40'}]
        etat = calcule(vide, paris(2027, 3, 27, 12))
        self.assertEqual(etat['ignorees'], [{'plage': 'c', 'jour': '2027-03-28', 'motif': 'intervalle_vide'}])
        self.assertIn('heure d’été', h.explications(etat)[0])
        self.assertFalse(calcule(vide, saut)['pause'])
        nuit = calcule(NUIT, paris(2027, 3, 27, 18))
        self.assertEqual(nuit['fin'], paris(2027, 3, 28, 7))                     # 7 h locales, pas « + 13 h »
        self.assertEqual(nuit['fin'] - paris(2027, 3, 27, 18), timedelta(hours=12))
        journee, _ = h.plages_utc(politique([{'id': 'f', 'days': [7], 'kind': 'full_day'}]), date(2027, 3, 28), date(2027, 3, 28))
        self.assertEqual(journee[0][1] - journee[0][0], timedelta(hours=23))

    def test_HO06_heure_repetee_en_automne(self):
        premier = paris(2027, 10, 31, 2, 30, fold=0)
        second = paris(2027, 10, 31, 2, 30, fold=1)
        self.assertEqual(second - premier, timedelta(hours=1))
        debut = [{'id': 'a', 'days': [7], 'kind': 'period', 'start': '02:30', 'end': '05:00'}]
        self.assertTrue(calcule(debut, premier)['pause'])                        # début ambigu : premier instant
        self.assertFalse(calcule(debut, premier - timedelta(seconds=1))['pause'])
        fin = [{'id': 'b', 'days': [6], 'kind': 'period', 'start': '22:00', 'end': '02:30'}]
        self.assertTrue(calcule(fin, premier + timedelta(minutes=10))['pause'])  # entre les deux occurrences : pas de reprise
        self.assertTrue(calcule(fin, second - timedelta(seconds=1))['pause'])    # fin ambiguë : dernier instant
        self.assertFalse(calcule(fin, second)['pause'])
        journee, _ = h.plages_utc(politique([{'id': 'f', 'days': [7], 'kind': 'full_day'}]), date(2027, 10, 31), date(2027, 10, 31))
        self.assertEqual(journee[0][1] - journee[0][0], timedelta(hours=25))

    def test_HO07_fuseau_independant_du_serveur(self):
        hiver = datetime(2026, 11, 9, 17, 0, tzinfo=timezone.utc)                # 18 h à Paris
        ancien = os.environ.get('TZ')
        try:
            os.environ['TZ'] = 'Asia/Tokyo'
            if hasattr(time, 'tzset'):
                time.tzset()
            self.assertTrue(calcule(NUIT, hiver)['pause'])
        finally:
            if ancien is None:
                os.environ.pop('TZ', None)
            else:
                os.environ['TZ'] = ancien
            if hasattr(time, 'tzset'):
                time.tzset()
        self.assertFalse(calcule(NUIT, hiver, 'America/Cayenne')['pause'])       # 14 h à Cayenne : même règle, autre fuseau
        self.assertEqual(calcule(NUIT, datetime(2026, 7, 1, 16, 0, tzinfo=timezone.utc))['pause'], True)   # 18 h en heure d'été : décalage réel, pas +1


class Validation(unittest.TestCase):
    def test_HO21_localtime_refuse_meme_si_le_systeme_le_lit(self):
        """5.6.90 : sous Linux, ZoneInfo('localtime') réussit ; le nom n'est pas dans la base IANA et doit être refusé."""
        from zoneinfo import ZoneInfo
        from agent import horaires_ia5689 as h
        h._noms_iana.cache_clear()
        self.addCleanup(h._noms_iana.cache_clear)
        # 5.6.91 : base simulée comme celle de Debian, qui liste ces noms du système
        with patch.object(h, 'available_timezones', lambda: {'Europe/Paris', 'UTC', 'localtime', 'Factory', 'posix/Europe/Paris'}), \
                patch.object(h, 'ZoneInfo', lambda nom: ZoneInfo('UTC')):
            for nom in ('localtime', 'Factory', 'posixrules', 'posix/Europe/Paris', 'right/UTC'):
                with self.assertRaises(Stop, msg=nom):
                    h._zone(nom)
            self.assertEqual(str(h._zone('Europe/Paris')), 'UTC')

    def test_HO21_validation_serveur_des_heures_du_fuseau_et_des_plages(self):
        ok = {'id': 'x', 'days': [1], 'kind': 'period', 'start': '18:00', 'end': '07:00'}
        cas = [({'id': 'x', 'days': [1], 'kind': 'period', 'start': '24:00', 'end': '07:00'}, 'horaires_heure_invalide'),
               ({'id': 'x', 'days': [1], 'kind': 'period', 'start': '18:00', 'end': '7h'}, 'horaires_heure_invalide'),
               ({'id': 'x', 'days': [], 'kind': 'period', 'start': '18:00', 'end': '07:00'}, 'horaires_jours_invalides'),
               ({'id': 'x', 'days': [8], 'kind': 'period', 'start': '18:00', 'end': '07:00'}, 'horaires_jours_invalides'),
               ({'id': 'x', 'days': [1, 1], 'kind': 'period', 'start': '18:00', 'end': '07:00'}, 'horaires_jours_invalides'),
               ({'id': 'x', 'days': [True], 'kind': 'period', 'start': '18:00', 'end': '07:00'}, 'horaires_jours_invalides'),
               ({'id': 'x', 'days': [1], 'kind': 'autre'}, 'horaires_format_invalide'),
               ({'id': 'x', 'days': [1], 'kind': 'full_day', 'start': '18:00'}, 'horaires_format_invalide')]
        for plage, code in cas:
            with self.assertRaisesRegex(Stop, code, msg=str(plage)):
                h.valider(politique([plage]))
        with self.assertRaisesRegex(Stop, 'horaires_plage_en_double'):
            h.valider(politique([ok, ok]))
        with self.assertRaisesRegex(Stop, 'horaires_trop_de_plages'):
            h.valider(politique([{**ok, 'id': str(i)} for i in range(51)]))
        for fuseau in ('Europe/Atlantide', '../etc/passwd', 'localtime', ''):
            with self.assertRaisesRegex(Stop, 'horaires_fuseau_inconnu', msg=fuseau):
                h.valider(politique([ok], fuseau=fuseau))

    def test_exemple_du_cahier_accepte(self):
        chemin = ROOT / 'tests' / 'contrats5689' / 'Pauses-horaires-IA-exemple.json'
        if not chemin.is_file():
            self.skipTest('cahier des charges absent')
        exemple = h.valider(json.loads(chemin.read_text(encoding='utf-8')))
        self.assertEqual([p['id'] for p in exemple['windows']], ['soir', 'nuit'])
        self.assertTrue(h.lire_horaire({h.CLE_POLITIQUE: json.dumps(exemple)}, paris(2026, 11, 10, 0))['pause'])


class Priorites(Base):
    def test_HO01_la_decision_unique_donne_motif_transition_fuseau_et_revision(self):
        self.activer()
        self.a(2026, 11, 9, 17, 59, 59)
        libre = h.evaluer(self.desk)
        self.assertEqual((libre['autorise'], libre['motifs'], libre['fuseau'], libre['revision']), (True, [], 'Europe/Paris', 1))
        self.assertEqual(libre['prochaine_transition_utc'], paris(2026, 11, 9, 18).isoformat())
        self.a(2026, 11, 9, 18)
        pause = h.evaluer(self.desk)
        self.assertEqual((pause['autorise'], pause['motifs'], pause['code']), (False, ['pause_horaire'], 'ia_en_pause_horaire'))
        self.assertEqual(pause['prochaine_transition_utc'], paris(2026, 11, 10, 7).isoformat())
        self.a(2026, 11, 10, 7)
        self.assertTrue(h.evaluer(self.desk)['autorise'])

    def test_HO02_aucun_appel_ni_remise_en_file_a_minuit(self):
        self.activer()
        self.a(2026, 11, 9, 18)
        h.surveiller(self.desk)
        jid = self.suspendre()
        for instant in ((2026, 11, 9, 23, 59, 59), (2026, 11, 10, 0), (2026, 11, 10, 0, 0, 1)):
            self.a(*instant)
            self.assertIsNone(h.surveiller(self.desk))
            self.assertEqual(self.statut(jid)['status'], 'suspended')
            self.assertEqual(arret5643.reason_to_stop(self.desk.c['state_dir']), 'ia_en_pause_horaire')
        self.assertEqual(arret5643.release_stale(self.desk), (0, 0))
        self.assertEqual([t['vers'] for t in self.transitions()], ['pause'])

    def test_HO08_a_7_h_la_pause_manuelle_reste_et_reprendre_ne_touche_pas_au_planning(self):
        self.activer()
        self.a(2026, 11, 9, 18)
        h.surveiller(self.desk)
        pause_all(self.desk, True)
        jid = self.suspendre()
        self.a(2026, 11, 10, 7)
        h.surveiller(self.desk)
        decision = h.evaluer(self.desk)
        self.assertEqual(decision['motifs'], ['pause_manuelle'])
        self.assertEqual(arret5643.reason_to_stop(self.desk.c['state_dir']), 'ia_en_pause')
        self.assertEqual(arret5643.release_stale(self.desk), (0, 0))             # le travail reste retenu : la pause manuelle a pris le relais
        self.assertEqual(self.statut(jid)['status'], 'suspended')
        self.a(2026, 11, 10, 20)                                                 # nouvelle plage en vigueur, l'avocat clique sur Reprendre
        out = pause_all(self.desk, False)
        self.assertIn('pause programmée reste en vigueur', out['message'])
        self.assertTrue(h.etat_public(self.desk)['politique']['enabled'])
        self.assertEqual(h.etat_public(self.desk)['revision'], 1)
        self.assertEqual(self.statut(jid)['status'], 'suspended')
        self.assertEqual(h.evaluer(self.desk)['motifs'], ['pause_horaire'])
        self.a(2026, 11, 11, 7)
        h.surveiller(self.desk)
        self.assertEqual(self.statut(jid)['status'], 'pending')

    def test_HO08_le_planning_ne_touche_jamais_au_booleen_de_pause_manuelle(self):
        self.activer()
        for instant in ((2026, 11, 9, 17), (2026, 11, 9, 18), (2026, 11, 10, 7)):
            self.a(*instant)
            h.surveiller(self.desk)
            self.assertFalse(self.desk.settings('missions567:pause', False))
        self.assertFalse(self.desk.db.execute("SELECT 1 FROM settings WHERE key='missions567:pause' AND value='true'").fetchone())

    def test_HO08_manuelle_avant_le_planning_dans_les_controles(self):
        self.activer()
        self.a(2026, 11, 9, 19)
        pause_all(self.desk, True)
        self.assertEqual(arret5643.reason_to_stop(self.desk.c['state_dir']), 'ia_en_pause')
        decision = h.evaluer(self.desk)
        self.assertEqual((decision['code'], decision['motifs']), ('ia_en_pause', ['pause_manuelle', 'pause_horaire']))

    def test_HO09_reprise_sans_lever_le_dossier_ni_les_annulations(self):
        self.activer()
        self.a(2026, 11, 9, 18)
        h.surveiller(self.desk)
        pause5629.set_pause(self.desk, MID, True)
        dossier = self.suspendre('refresh_brief', {'matter': MID})
        parent = self.suspendre('refresh_brief', {'matter': 'AUTRE-1'})
        enfant = self.suspendre('monitor_matter', {'matter': 'AUTRE-2'})
        libre = self.suspendre('monitor_matter', {'matter': 'AUTRE-3'})
        self.desk.db.execute('UPDATE jobs SET parent_job_id=? WHERE id=?', (parent, enfant))
        self.desk.db.commit()
        self.desk.cancel_job(parent)
        self.assertEqual(self.statut(enfant)['status'], 'cancelled')
        self.a(2026, 11, 10, 7)
        h.surveiller(self.desk)
        self.assertEqual(self.statut(dossier)['status'], 'suspended')            # la pause du dossier reste
        self.assertEqual((self.statut(parent)['status'], self.statut(enfant)['status']), ('cancelled', 'cancelled'))
        self.assertEqual(self.statut(libre)['status'], 'pending')
        self.assertEqual(arret5643.release_stale(self.desk), (0, 0))
        self.assertEqual(self.statut(dossier)['status'], 'suspended')
        self.assertEqual(h.evaluer(self.desk, matter=MID, automatic=True)['motifs'], ['pause_dossier'])
        pause5629.set_pause(self.desk, MID, False)
        self.assertEqual(self.statut(dossier)['status'], 'pending')

    def test_HO09_annulation_avant_le_motif_horaire(self):
        self.activer()
        self.a(2026, 11, 9, 19)
        jid = self.desk.enqueue('assistant_answer', {'question': 'Fictive', 'matter': MID}, origin='user')
        self.desk.cancel_job(jid)
        decision = h.evaluer(self.desk, job_id=jid, matter=MID)
        self.assertEqual((decision['motifs'][0], decision['code']), ('annulation', 'operation_annulee'))

    def test_HO10_derogation_limitee_au_planning_au_perimetre_et_a_la_duree(self):
        self.activer()
        self.a(2026, 11, 9, 19)
        h.surveiller(self.desk)
        with self.assertRaisesRegex(Stop, 'derogation_perimetre_invalide'):
            h.accorder_derogation(self.desk, 'admin@example.test', 'tout')
        demande = self.suspendre('assistant_answer', {'question': 'Fictive'}, origin='user')
        auto = self.suspendre('refresh_brief', {'matter': MID})
        etat = h.accorder_derogation(self.desk, 'admin@example.test', 'demandes')
        self.assertEqual(etat['derogation']['fin'], (self.horloge.instant + timedelta(minutes=30)).isoformat())
        self.assertEqual(etat['derogation']['par'], 'admin@example.test')
        self.assertEqual(self.statut(demande)['status'], 'pending')              # dans le périmètre : reprise
        self.assertEqual(self.statut(auto)['status'], 'suspended')               # hors périmètre : reste en pause
        self.assertEqual(arret5643.reason_to_stop(self.desk.c['state_dir']), '')
        self.assertEqual(h.code_horaire(self.desk, automatic=True), 'ia_en_pause_horaire')
        self.assertTrue(arret5643.global_paused(self.desk))                      # les automatismes restent arrêtés
        self.desk.setting(arret5643.PAUSE_GLOBAL, True)                          # ne lève pas une pause manuelle
        self.assertEqual(arret5643.reason_to_stop(self.desk.c['state_dir']), 'ia_en_pause')
        self.desk.setting(arret5643.PAUSE_GLOBAL, False)
        pause5629.set_pause(self.desk, MID, True)                                # ni celle d'un dossier
        with arret5643.job_context(self.desk.c['state_dir'], None, MID, True):
            self.assertEqual(arret5643.reason_to_stop(self.desk.c['state_dir']), 'agent_en_pause_sur_ce_dossier')
        pause5629.set_pause(self.desk, MID, False)
        self.horloge.instant += timedelta(minutes=30)                            # échéance : la pause est recalculée
        self.assertEqual(arret5643.reason_to_stop(self.desk.c['state_dir']), 'ia_en_pause_horaire')
        h.surveiller(self.desk)
        self.assertEqual(self.statut(demande)['status'], 'suspended')
        self.assertIsNone(h.etat_public(self.desk)['derogation'])
        self.assertIsNone(self.desk.settings(h.CLE_DEROGATION))

    def test_HO10_aucun_droit_supplementaire_ni_levee_de_securite(self):
        self.activer()
        self.a(2026, 11, 9, 19)
        h.accorder_derogation(self.desk, 'admin@example.test', 'cabinet')
        decision = h.evaluer(self.desk, automatic=True, args={'mission_id': 'mission-inconnue'})
        self.assertEqual((decision['autorise'], decision['motifs'], decision['code']), (False, ['droits'], 'autorisation_mission_absente'))
        self.desk.db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)', (h.CLE_POLITIQUE, '{illisible'))
        self.desk.db.commit()
        self.assertEqual(arret5643.reason_to_stop(self.desk.c['state_dir']), 'horaires_ia_illisibles')   # l'incident n'est pas levé par la dérogation
        with self.assertRaisesRegex(Stop, 'horaires_ia_illisibles'):
            h.accorder_derogation(self.desk, 'admin@example.test', 'cabinet')

    def test_HO10_perimetre_dossier_et_cle_d_idempotence(self):
        self.activer()
        self.a(2026, 11, 9, 19)
        with self.assertRaisesRegex(Stop, 'dossier_absent'):
            h.accorder_derogation(self.desk, 'admin@example.test', 'dossier', 'DOS-INEXISTANT')
        premier = h.accorder_derogation(self.desk, 'admin@example.test', 'dossier', MID, 'clic-1')
        self.assertEqual(h.accorder_derogation(self.desk, 'admin@example.test', 'dossier', MID, 'clic-1')['derogation']['id'], premier['derogation']['id'])
        self.assertEqual(self.desk.db.execute("SELECT COUNT(*) FROM audit WHERE action='horaires_ia5689_derogation'").fetchone()[0], 1)
        self.assertEqual(h.code_horaire(self.desk, matter=MID, automatic=True), '')
        self.assertEqual(h.code_horaire(self.desk, matter='DOS-AUTRE', automatic=True), 'ia_en_pause_horaire')
        self.assertEqual(h.code_horaire(self.desk), 'ia_en_pause_horaire')       # appel sans dossier : hors périmètre

    def test_HO10_sans_pause_en_cours_rien_a_autoriser(self):
        self.activer()
        with self.assertRaisesRegex(Stop, 'aucune_pause_programmee_en_cours'):
            h.accorder_derogation(self.desk, 'admin@example.test')

    def test_HO11_redemarrage_a_20_h_ia_bloquee_a_8_h_reprise_unique(self):
        self.activer()
        self.a(2026, 11, 9, 17)
        h.surveiller(self.desk)
        jid = self.desk.enqueue('refresh_brief', {'matter': MID}, origin='automatic')
        self.a(2026, 11, 9, 20)                                                  # service arrêté à 17 h, redémarré à 20 h
        retour = Desk(self.f.c)
        self.addCleanup(retour.db.close)
        self.assertEqual(arret5643.reason_to_stop(self.desk.c['state_dir']), 'ia_en_pause_horaire')   # avant toute boucle : bloquée immédiatement
        with self.assertRaisesRegex(Stop, 'ia_en_pause_horaire'):
            pause5629.check_running(retour, retour.db.execute('SELECT * FROM jobs WHERE id=?', (jid,)).fetchone(), {'matter': MID})
        self.assertEqual(h.surveiller(retour)['vers'], 'pause')
        self.assertEqual(self.statut(jid, retour)['status'], 'suspended')
        self.a(2026, 11, 12, 8)                                                  # plusieurs nuits manquées
        self.assertEqual(h.surveiller(retour)['repris'], 1)
        self.assertEqual([t['vers'] for t in self.transitions()], ['pause', 'libre'])   # une seule reprise, pas une par nuit
        self.assertEqual(self.statut(jid, retour)['status'], 'pending')
        self.assertIsNone(h.surveiller(retour))

    def test_HO11_arret_avant_la_nuit_retour_le_lendemain_aucune_transition(self):
        self.activer()
        self.a(2026, 11, 9, 17)
        h.surveiller(self.desk)
        self.a(2026, 11, 10, 8)
        self.assertIsNone(h.surveiller(self.desk))
        self.assertEqual(self.transitions(), [])

    def test_HO12_demande_de_17_h_55_conservee_jusqu_a_7_h(self):
        self.activer()
        self.a(2026, 11, 9, 17, 55)
        depot = self.horloge.instant
        jid = self.desk.enqueue('assistant_answer', {'question': 'Fictive'}, origin='user')
        self.desk.db.execute('UPDATE jobs SET created=? WHERE id=?', (depot.isoformat(), jid))
        self.desk.db.commit()
        self.a(2026, 11, 9, 18)
        h.surveiller(self.desk)
        self.assertEqual(self.statut(jid)['stop_reason'], 'pause_horaire')
        self.a(2026, 11, 10, 6, 59)
        self.assertEqual(arret5643.release_stale(self.desk), (0, 0))             # treize heures de pause : pas obsolète
        self.a(2026, 11, 10, 7)
        self.assertEqual(arret5643.release_stale(self.desk), (1, 0))             # même si la reprise passe par le nettoyage
        ligne = self.desk.db.execute('SELECT status,result,pause_debut,pause_secondes FROM jobs WHERE id=?', (jid,)).fetchone()
        self.assertEqual((ligne['status'], ligne['pause_debut']), ('pending', None))
        self.assertEqual(ligne['pause_secondes'], 13 * 3600)                     # durée enregistrée, distincte de l'ancienneté active

    def test_HO12_echeance_absolue_toujours_controlee(self):
        self.activer()
        self.a(2026, 11, 9, 19)
        h.surveiller(self.desk)
        echue = self.suspendre('assistant_answer', {'question': 'Fictive 1', 'echeance_absolue': paris(2026, 11, 10, 6).isoformat()}, origin='user')
        valable = self.suspendre('assistant_answer', {'question': 'Fictive 2', 'echeance_absolue': paris(2026, 11, 10, 9).isoformat()}, origin='user')
        self.a(2026, 11, 10, 7)
        h.surveiller(self.desk)
        ligne = self.desk.db.execute('SELECT status,result FROM jobs WHERE id=?', (echue,)).fetchone()
        self.assertEqual(ligne['status'], 'cancelled')                           # jamais repris seul
        self.assertTrue(json.loads(ligne['result'])['revalidation_requise'])
        self.assertEqual(self.statut(valable)['status'], 'pending')

    def test_HO12_anciennete_active_au_dela_de_six_heures(self):
        self.activer()
        self.a(2026, 11, 10, 7)
        ancienne = self.suspendre('assistant_answer', {'question': 'Fictive'}, origin='user', created=paris(2026, 11, 9, 8).isoformat(), debut=paris(2026, 11, 9, 18))
        self.assertEqual(arret5643.release_stale(self.desk), (0, 1))             # dix heures d'activité avant la pause : close, la pause n'y est pour rien
        self.assertEqual(self.statut(ancienne)['status'], 'cancelled')

    def test_HO12_automatisme_suspendu_conserve_et_pause_ordinaire_inchangee(self):
        self.activer()
        self.a(2026, 11, 10, 7)
        auto = self.suspendre('refresh_brief', {'matter': MID}, created=paris(2026, 11, 9, 17, 50).isoformat(), debut=paris(2026, 11, 9, 18))
        self.assertEqual(arret5643.release_stale(self.desk), (1, 0))
        self.assertEqual(self.statut(auto)['status'], 'pending')                 # l'événement de la nuit n'est pas perdu

    def test_HO12_attente_programmee_ne_declenche_pas_le_secours_externe(self):
        cree = paris(2026, 11, 9, 17, 55).isoformat()
        ligne = {'created': cree, 'pause_secondes': 13 * 3600}
        self.assertEqual(arret5643.active_created(ligne), paris(2026, 11, 10, 6, 55).isoformat())
        self.assertEqual(arret5643.active_created({'created': cree, 'pause_secondes': 0}), cree)


# ---------------------------------------------------------------- § 30 : reprise et exploitation
class FauxTransport:
    """Remplace ``HTTP.json`` : garde les appels ; ``/api/chat`` peut déclencher un effet."""
    def __init__(self):
        self.appels = []
        self.sur_chat = None

    def __call__(self, http, method, path, data=None, **kw):
        self.appels.append(path)
        if path == '/api/show':
            return {'capabilities': ['completion']}
        if path == '/api/tags':
            return {'models': [{'name': 'modele-fictif'}]}
        if path == '/api/chat' and self.sur_chat:
            return self.sur_chat()
        if path == '/api/embed':
            return {'embeddings': [[0.1] * 8 for _ in data['input']]}
        if path == '/api/generate':
            return {'response': 'prêt'}
        if path == '/chat/completions':
            return {'choices': [{'message': {'content': '{"ok": true}'}}], 'usage': {}}
        return {'done': True, 'done_reason': 'stop', 'message': {'content': '{"ok": true}'}}

    def modeles(self):
        return [a for a in self.appels if a not in ('/api/show', '/api/tags')]


class Chemins(routeur.Strict):
    def setUp(self):
        super().setUp()
        self.horloge = Horloge()
        p = patch.object(h, 'maintenant', self.horloge)
        p.start()
        self.addCleanup(p.stop)
        self.transport = FauxTransport()
        transport = self.transport
        q = patch.object(HTTP, 'json', lambda http, method, path, data=None, **kw: transport(http, method, path, data, **kw))
        q.start()
        self.addCleanup(q.stop)

    def planifier(self):
        h.enregistrer(self.desk, {'revision': 0, 'enabled': True, 'timezone': 'Europe/Paris', 'windows': NUIT}, 'admin@example.test', confirmer=True)
        self.horloge.instant = paris(2026, 11, 9, 19)

    def locale(self, **extra):
        return {'provider_type': 'ollama', 'provider_id': 'ollama', 'url': 'http://127.0.0.1:11434', 'model': 'modele-fictif', 'state_dir': self.desk.c['state_dir'], 'purpose': 'assistant', **extra}

    def externe(self, genre, url):
        ai_gateway.save_provider(self.desk, genre, genre, url, 'modele-fictif', 'cle-fictive', True, True)
        p = patch('agent.policy567.check_external')                              # la politique des données a ses propres essais ; ici, la pause seule
        p.start()
        self.addCleanup(p.stop)
        return dict(ai_gateway.provider_registry(self.desk.c)[genre], provider_id=genre, provider_type=genre, input_usd_per_million=1, output_usd_per_million=1, monthly_budget_usd=100)

    def bloque(self, appel):
        self.transport.appels.clear()
        with self.assertRaises(Stop) as arret:
            appel()
        self.assertEqual(str(arret.exception), 'ia_en_pause_horaire')
        self.assertEqual(self.transport.modeles(), [])

    def test_HO13_controle_positif_sans_pause_le_faux_transport_recoit_l_appel(self):
        Model(self.locale()).complete([{'role': 'user', 'content': 'Essai fictif'}], max_tokens=5)
        self.assertEqual(self.transport.modeles(), ['/api/chat'])

    def test_HO13_ollama(self):
        modele_local = Model(self.locale())
        self.planifier()
        self.bloque(lambda: modele_local.complete([{'role': 'user', 'content': 'Essai fictif'}], max_tokens=5))

    def test_HO13_mistral(self):
        cfg = self.externe('mistral', 'https://api.mistral.ai/v1')
        self.assertTrue(cfg.get('state_dir'))                                    # sinon le contrôle serait ouvert
        modele_externe = Model(cfg)
        self.planifier()
        self.bloque(lambda: modele_externe.complete([{'role': 'user', 'content': 'Essai fictif'}], max_tokens=5))

    def test_HO13_openai(self):
        modele_externe = Model(self.externe('openai', 'https://api.openai.com/v1'))
        self.planifier()
        self.bloque(lambda: modele_externe.complete([{'role': 'user', 'content': 'Essai fictif'}], max_tokens=5))

    def test_HO13_routeur_et_abonnement(self):
        self.strict()
        self.planifier()
        with self.assertRaises(Stop) as arret:
            self.model().ask('triage', {'subject': 'Objet fictif'})
        self.assertEqual(str(arret.exception), 'ia_en_pause_horaire')
        self.assertEqual(self.fake.sent, [])
        avant = len(routage5682.recent(self.desk.c['state_dir']))
        with self.assertRaisesRegex(Stop, 'ia_en_pause_horaire'):
            routage5682.decide(self.desk.c, 'mail_triage', 'triage', {'text': 'x'})
        self.assertEqual(len(routage5682.recent(self.desk.c['state_dir'])), avant)

    def test_HO13_repli_du_routeur_ne_contourne_pas_la_pause(self):
        """La plage commence pendant l'appel local : le fournisseur de secours n'est pas sollicité."""
        h.enregistrer(self.desk, {'revision': 0, 'enabled': True, 'timezone': 'Europe/Paris', 'windows': NUIT}, 'admin@example.test', confirmer=True)
        self.horloge.instant = paris(2026, 11, 9, 17, 59)
        secours = self.externe('mistral', 'https://api.mistral.ai/v1')

        def lent():
            self.horloge.instant = paris(2026, 11, 9, 18)
            raise Stop('generation_ia_delai_depasse')
        self.transport.sur_chat = lent
        modele_secours = Model(self.locale(secours={'delay': 180, 'candidates': [secours]}))
        with self.assertRaises(Stop) as arret:
            modele_secours.complete([{'role': 'user', 'content': 'Essai fictif'}], max_tokens=5)
        self.assertEqual(str(arret.exception), 'ia_en_pause_horaire')
        self.assertEqual(self.transport.modeles(), ['/api/chat'])                # l'appel local seulement, jamais /chat/completions

    def test_HO13_embeddings(self):
        ollama, reglages = {'url': 'http://127.0.0.1:11434'}, {'enabled': True, 'embedding_model': 'empreinte-fictive'}
        with patch.dict(rag._CHECKED, clear=True):
            calcul_empreintes = rag.Embedder(ollama, reglages, self.desk.c['state_dir'])
        self.assertEqual(len(calcul_empreintes.embed(['texte fictif'])), 1)
        self.planifier()
        self.bloque(lambda: calcul_empreintes.embed(['texte fictif']))

    def test_HO13_embeddings_de_l_index_recherche_sans_empreinte(self):
        index = DocumentIndex(self.desk.c['state_dir'], {'enabled': True, 'embedding_model': 'empreinte-fictive'}, {'url': 'http://127.0.0.1:11434'})
        self.addCleanup(index.db.close)
        self.planifier()
        with patch.dict(rag._CHECKED, clear=True), patch('agent.politique5682.embeddings_allowed', return_value=True):
            self.transport.appels.clear()
            vecteur, erreur = index.query_vector(['terme fictif'])
        self.assertEqual((vecteur, erreur), (None, 'ia_en_pause_horaire'))       # la recherche continue en plein texte
        self.assertNotIn('/api/embed', self.transport.appels)

    def test_HO13_controle_de_qualite(self):
        principal, controle = self.locale(model='modele-a'), self.locale(model='modele-b', purpose='control')
        jid = self.desk.enqueue('assistant_answer', {'question': 'Fictive'}, origin='user')
        self.planifier()
        with patch('agent.model.routed_config', side_effect=lambda c, p: controle if p == 'control' else principal), \
                patch('agent.hybrid400.choose', return_value={'external': False}):
            self.bloque(lambda: relevance370.review_result(self.desk, jid, 'draft_act', {'reponse': 'Texte fictif'}))

    def test_HO13_diagnostic_avec_inference(self):
        self.planifier()
        self.transport.appels.clear()
        cfg = {'state_dir': self.desk.c['state_dir'], 'ollama': {'url': 'http://127.0.0.1:11434', 'model': 'modele-fictif'}}
        with patch.object(guide5631, '_record', side_effect=lambda d, data, c, cap, statut, code, *r: {'capacite': cap, 'statut': statut, 'code': code}):
            resultats = guide5631._ai_tests(self.desk, {}, cfg, 'generate')
        self.assertEqual(resultats[-1], {'capacite': 'generate', 'statut': 'refused', 'code': 'ia_en_pause_horaire'})
        self.assertNotIn('/api/generate', self.transport.appels)
        sortie = profil5634.neutral_test(cfg)
        self.assertEqual(sortie['erreur'], 'ia_en_pause_horaire')
        self.assertNotIn('/api/chat', self.transport.appels)
        with self.assertRaisesRegex(Stop, 'ia_en_pause_horaire'):
            with patch('agent.model.routed_config', return_value=self.locale()):
                queue521.check_ai(self.desk)
        self.assertNotIn('/api/chat', self.transport.appels)

    def test_HO13_le_controle_de_sante_reste_sans_inference(self):
        self.planifier()
        self.transport.appels.clear()
        etat = modele.provider_diagnostic(self.locale())
        self.assertEqual(etat['status'], 'ok')                                   # service sans IA préservé
        self.assertEqual(self.transport.modeles(), [])


class Reprise(Base):
    def travail_en_cours(self, kind='refresh_brief', args=None, origin='automatic'):
        args = {'matter': MID} if args is None else args
        jid = self.desk.enqueue(kind, args, origin=origin)
        self.desk.db.execute("UPDATE jobs SET status='running',attempts=1,started=? WHERE id=?", (self.desk.now(), jid))
        self.desk.db.commit()
        return jid

    def test_HO14_reponse_tardive_conservee_seulement_comme_partiel(self):
        self.activer()
        self.a(2026, 11, 9, 17, 59)
        jid = self.travail_en_cours()

        def appel():
            self.a(2026, 11, 9, 18, 0, 1)
            return {'message': {'content': 'Réponse fictive arrivée trop tard'}}
        with arret5643.job_context(self.desk.c['state_dir'], jid, MID, True):
            with self.assertRaisesRegex(Stop, 'ia_en_pause_horaire'):
                arret5643.interruptible(appel, {'state_dir': self.desk.c['state_dir']}, poll=0.05)
        ligne = self.desk.db.execute('SELECT * FROM reponses_tardives5689').fetchone()
        self.assertEqual((ligne['job_id'], ligne['etat'], ligne['motif']), (jid, 'partielle', 'ia_en_pause_horaire'))
        self.assertIn('trop tard', ligne['contenu'])
        self.assertEqual(h.etat_public(self.desk)['reponses_tardives'], 1)
        h.apres_travail(self.desk, jid, 'done')                                  # repris et recalculé : la réponse tardive n'a plus d'objet
        self.assertEqual(self.desk.db.execute('SELECT etat FROM reponses_tardives5689').fetchone()[0], 'remplacee')

    def test_HO14_interruption_demandee_et_journal_veridique(self):
        self.activer()
        self.a(2026, 11, 9, 17, 59)
        jid = self.travail_en_cours()
        fin = threading.Event()
        self.addCleanup(fin.set)
        threading.Timer(0.15, lambda: self.a(2026, 11, 9, 18)).start()
        with arret5643.job_context(self.desk.c['state_dir'], jid, MID, True):
            with self.assertRaisesRegex(Stop, 'ia_en_pause_horaire'):
                arret5643.interruptible(lambda: fin.wait(4), {'state_dir': self.desk.c['state_dir']}, poll=0.05)
        note = json.loads(self.desk.db.execute("SELECT data FROM audit WHERE action='arret5643_inference' ORDER BY id DESC").fetchone()[0])
        self.assertEqual(note['motif'], 'ia_en_pause_horaire')
        self.assertIn('arrêt demandé', note['etat'])
        self.assertNotIn('calcul arrêté', note['etat'])                          # aucune affirmation sur le calcul chez le fournisseur

    def test_HO14_travail_suspendu_sans_tentative_ni_coupe_circuit(self):
        self.activer()
        self.a(2026, 11, 9, 19)
        ids = []
        with patch.object(pause5629, 'check_enqueue'):
            for numero in range(4):
                ids.append(self.desk.enqueue('refresh_brief', {'matter': MID, 'n': numero}, origin='automatic'))
        for jid in ids:
            self.assertTrue(self.desk.work_once())
            ligne = self.statut(jid)
            self.assertEqual((ligne['status'], ligne['stop_reason'], ligne['attempts']), ('suspended', 'pause_horaire', 0))
        self.assertEqual(pause5629.failures(self.desk, 'refresh_brief', MID), (0, ''))
        self.a(2026, 11, 10, 7)
        pause5629.check_enqueue(self.desk, 'refresh_brief', {'matter': MID}, 50, 'automatic')   # le coupe-circuit reste ouvert

    def test_HO14_demande_active_arretee_au_point_coherent(self):
        self.activer()
        self.a(2026, 11, 9, 17, 59)
        jid = self.desk.enqueue('assistant_answer', {'question': 'Fictive'}, origin='user')

        def travail(*args):
            self.a(2026, 11, 9, 18)
            pause5629.checkpoint(self.desk)
            self.fail('effet produit après le début de la pause')
        with patch.object(Desk, 'perform', travail):
            self.assertTrue(self.desk.work_once())
        ligne = self.statut(jid)
        self.assertEqual((ligne['status'], ligne['stop_reason'], ligne['attempts']), ('suspended', 'pause_horaire', 0))
        evenement = self.desk.db.execute("SELECT message FROM live_events_v430 WHERE job_id=? ORDER BY id DESC", (jid,)).fetchone()
        self.assertIn('pause programmée', evenement[0])                          # statut véridique : ni échec ni annulation

    def test_HO14_automatisme_actif_suspendu_jamais_annule(self):
        self.activer()
        self.a(2026, 11, 9, 17, 59)
        jid = self.desk.enqueue('refresh_brief', {'matter': MID}, origin='automatic')

        def travail(*args):
            self.a(2026, 11, 9, 18)
            h.surveiller(self.desk)                                              # transition vue par le superviseur pendant le travail
            self.assertEqual(self.statut(jid)['status'], 'cancel_requested')
            pause5629.checkpoint(self.desk)
            self.fail('effet produit après le début de la pause')
        with patch.object(Desk, 'perform', travail):
            self.assertTrue(self.desk.work_once())
        ligne = self.statut(jid)
        self.assertEqual((ligne['status'], ligne['stop_reason'], ligne['attempts']), ('suspended', 'pause_horaire', 0))
        self.a(2026, 11, 10, 7)
        h.surveiller(self.desk)
        self.assertEqual(self.statut(jid)['status'], 'pending')

    def test_HO14_annulation_deja_posee_jamais_remplacee(self):
        self.activer()
        self.a(2026, 11, 9, 17, 59)
        jid = self.desk.enqueue('assistant_answer', {'question': 'Fictive'}, origin='user')

        def travail(*args):
            self.desk.cancel_job(jid)
            self.a(2026, 11, 9, 18)
            raise Stop('ia_en_pause_horaire')
        with patch.object(Desk, 'perform', travail):
            self.desk.work_once()
        self.assertEqual(self.statut(jid)['status'], 'cancelled')
        self.a(2026, 11, 10, 7)
        h.surveiller(self.desk)
        self.assertEqual(self.statut(jid)['status'], 'cancelled')

    def test_HO15_aucun_effet_apres_le_controle_sauvegarde_possible(self):
        self.activer()
        self.a(2026, 11, 9, 17)
        jid = self.travail_en_cours('refresh_brief', {'matter': MID}, 'user')
        self.desk.active_job_id = jid
        self.addCleanup(setattr, self.desk, 'active_job_id', None)
        pause5629.checkpoint(self.desk)
        self.a(2026, 11, 9, 18)
        with self.assertRaisesRegex(Stop, 'ia_en_pause_horaire'):
            pause5629.checkpoint(self.desk)
        self.desk.active_job_id = None
        sauvegarde = self.desk.enqueue('sauvegarde5688', {'origine': 'essai'}, origin='automatic')
        self.assertEqual(self.statut(sauvegarde)['status'], 'pending')           # sans IA : ni refusé ni différé
        pause5629.check_running(self.desk, self.desk.db.execute('SELECT * FROM jobs WHERE id=?', (sauvegarde,)).fetchone(), {})

    def test_HO15_la_transition_laisse_passer_les_travaux_sans_ia(self):
        self.activer()
        self.a(2026, 11, 9, 17)
        sauvegarde = self.desk.enqueue('sauvegarde5688', {'origine': 'essai'}, origin='automatic')
        sante = self.desk.enqueue('health', {}, origin='automatic')
        analyse = self.desk.enqueue('refresh_brief', {'matter': MID}, origin='automatic')
        self.a(2026, 11, 9, 18)
        h.surveiller(self.desk)
        self.assertEqual((self.statut(sauvegarde)['status'], self.statut(sante)['status'], self.statut(analyse)['status']), ('pending', 'pending', 'suspended'))

    def test_HO15_rangement_des_notes_B07_s_arrete_entre_deux_notes(self):
        """Le contrôle est celui du lot stabilité (B07) : la pause programmée s'y applique sans code propre."""
        self.activer()
        self.a(2026, 11, 9, 17, 59)
        jid = self.travail_en_cours('ranger_notes5666', {'items': ['mail:1:7', 'mail:1:8']})
        self.desk.active_job_id = jid
        self.addCleanup(setattr, self.desk, 'active_job_id', None)
        racine = self.root / 'notes'
        (racine / 'Dossiers' / 'DEMO').mkdir(parents=True)
        from agent.dav import LocalFolder
        from agent import cockpit530
        dav = LocalFolder({'local_path': str(racine), 'roots': ['/Dossiers']})
        cache = {'ok': True, 'validity': '1', 'items': [{'uid': str(u), 'subject': '[À traiter] Note fictive ' + str(u), 'to': 'cabinet@example.test', 'agent': True} for u in (7, 8)]}
        deplaces = []

        def tri(desk, uid, validity):
            deplaces.append(uid)
            self.a(2026, 11, 9, 18)
            return True
        with arret5643.job_context(self.desk.c['state_dir'], jid, '', True), patch.object(cockpit530, '_drafts', return_value=cache), \
                patch.object(cockpit530, '_draft_matter', return_value='DOS-001'), patch('agent.drafts440.get_draft', return_value={'body': 'Note purement fictive.'}):
            with self.assertRaisesRegex(Stop, 'ia_en_pause_horaire'):
                notes5666.file_drafts(self.desk, {'items': ['mail:1:7', 'mail:1:8']}, dav=dav, trash=tri)
        self.assertEqual(deplaces, ['7'])
        self.assertEqual(len(list(racine.rglob('*.docx'))), 1)

    def test_HO16_courriels_de_la_nuit_ni_oublies_ni_analyses_deux_fois(self):
        self.activer()
        self.a(2026, 11, 9, 22)
        self.f.box.inputs = {'1': fixtures.mail(uid='1'), '2': fixtures.mail(uid='2')}
        self.assertEqual(self.f.engine.run(), {'examined': 0, 'skipped': 'ia_en_pause_horaire'})
        self.assertEqual(self.f.model.calls, [])
        self.assertEqual(self.f.engine.state.counts(), {})                       # aucun curseur « Analyse terminée » n'a avancé
        self.a(2026, 11, 10, 7)
        self.assertEqual(self.f.engine.run()['examined'], 2)
        analyses = len(self.f.model.calls)
        self.assertGreater(analyses, 0)
        self.assertEqual(len(self.f.box.appended), 2)
        self.f.engine.run()
        self.assertEqual(len(self.f.model.calls), analyses)                      # pas de double analyse

    def test_HO16_courriel_en_cours_quand_la_pause_commence(self):
        self.activer()
        self.a(2026, 11, 9, 17, 59)
        self.f.model.after_compose = lambda: self.a(2026, 11, 9, 18)
        self.f.engine.process(fixtures.mail(uid='3'))
        self.assertEqual(self.f.box.appended, [])                                # la réponse n'est pas déposée
        self.a(2026, 11, 10, 7)
        self.f.model.after_compose = None
        self.f.box.inputs = {'3': fixtures.mail(uid='3')}
        self.f.engine.run()
        self.assertEqual(len(self.f.box.appended), 1)                            # et le courriel n'est pas perdu

    def test_pause_manuelle_pendant_un_courriel_il_reste_a_traiter(self):
        """Défaut relevé pendant le lot : une pause manuelle en plein traitement écartait le courriel sans reprise."""
        self.f.model.after_compose = lambda: self.desk.setting('missions567:pause', True)
        self.f.engine.process(fixtures.mail(uid='5'))
        self.assertEqual(self.f.box.appended, [])
        self.assertEqual(self.f.engine.state.rows(10)[0][1], 'retry')
        self.f.model.after_compose = None
        self.desk.setting('missions567:pause', False)
        self.f.box.inputs = {'5': fixtures.mail(uid='5')}
        self.f.engine.run()
        self.assertEqual(len(self.f.box.appended), 1)

    def test_HO16_pause_juste_avant_le_depot_du_brouillon(self):
        """Dernier contrôle avant l'écriture IMAP : la réponse n'est pas déposée et le courriel reste à traiter."""
        self.activer()
        self.a(2026, 11, 9, 17, 59)

        def avant_depot(state_dir, brouillon):
            self.a(2026, 11, 9, 18)                                              # entre la préparation du brouillon et le dernier contrôle
            return brouillon
        with patch('agent.courrier5632.add_agent_bcc', avant_depot):
            self.f.engine.process(fixtures.mail(uid='4'))
        self.assertEqual(self.f.box.appended, [])
        self.assertEqual(self.f.engine.state.rows(10)[0][1:3], ('retry', 'ia_en_pause_horaire'))   # ni écarté ni « analyse terminée »
        self.a(2026, 11, 10, 7)
        self.f.box.inputs = {'4': fixtures.mail(uid='4')}
        self.f.engine.run()
        self.assertEqual(len(self.f.box.appended), 1)

    def test_HO16_evenement_enregistre_une_fois_et_differe(self):
        self.activer()
        self.a(2026, 11, 9, 22)
        args = {'matter': MID, 'source': 'evenement-fictif-1'}
        premier = self.desk.enqueue('refresh_brief', args, origin='automatic')
        self.assertEqual(self.desk.enqueue('refresh_brief', args, origin='automatic'), premier)   # clé d'idempotence : le déclencheur
        self.assertEqual(self.desk.db.execute("SELECT COUNT(*) FROM jobs WHERE kind='refresh_brief'").fetchone()[0], 1)
        self.assertEqual(self.statut(premier)['status'], 'suspended')
        self.assertEqual(self.desk.db.execute("SELECT COUNT(*) FROM live_events_v430 WHERE job_id=?", (premier,)).fetchone()[0], 0)
        self.a(2026, 11, 10, 7)
        h.surveiller(self.desk)
        self.assertEqual(self.statut(premier)['status'], 'pending')

    def test_HO16_lot_transactionnel_differe_sans_perte(self):
        self.activer()
        self.a(2026, 11, 9, 22)
        ids = self.desk.admit([('monitor_matter', {'matter': MID}, 60)])
        self.assertEqual(self.statut(ids[0])['stop_reason'], 'pause_horaire')
        self.assertEqual(self.desk.admit([('monitor_matter', {'matter': MID}, 60)]), ids)

    def test_HO16_surveillance_sans_lecture_pendant_la_plage(self):
        self.activer()
        self.a(2026, 11, 9, 22)
        self.assertEqual(watch430.schedule(self.desk, 1_000_000_000), [])
        self.assertFalse(self.desk.db.execute("SELECT 1 FROM jobs WHERE kind IN ('live_mail430','live_documents430')").fetchone())
        self.a(2026, 11, 10, 7)
        h.surveiller(self.desk)
        jobs = watch430.schedule(self.desk, 1_000_000_000)
        self.assertTrue(jobs)
        self.assertEqual(self.desk.db.execute("SELECT COUNT(*) FROM jobs WHERE kind='live_mail430'").fetchone()[0], 1)

    def routine(self):
        forme = {'title': 'Routine fictive', 'objective': 'Point du matin', 'task_type': 'nouveautes', 'freq': 'quotidien', 'time': '20:00', 'scope': 'cabinet', 'dossiers': MID, 'request_key': 'cle-routine-5689'}
        try:
            return rt.create(self.desk, 'avocat@example.test', 'avocat', forme)
        except Stop:
            return rt.create(self.desk, 'admin@example.test', 'administrateur', forme)

    def test_HO17_routine_differee_identifiable_sans_travail_par_passage(self):
        depart = self.a(2026, 11, 9, 12)
        rt.tick(self.desk, depart)                                               # premier passage : rien n'est rattrapé
        self.routine()
        self.activer()
        for minute in (1, 2, 3):
            instant = self.a(2026, 11, 9, 20, minute)
            rt.tick(self.desk, instant)
        occurrence = self.desk.db.execute('SELECT state,error_code,attempts FROM routines5652_occ').fetchall()
        self.assertEqual(len(occurrence), 1)
        self.assertEqual((occurrence[0]['state'], occurrence[0]['error_code'], occurrence[0]['attempts']), ('reportee_pause', 'pause_horaire', 0))
        self.assertFalse(self.desk.db.execute("SELECT 1 FROM jobs WHERE kind='routine5652'").fetchone())
        instant = self.a(2026, 11, 10, 7)
        h.surveiller(self.desk)
        rt.tick(self.desk, instant)
        rt.tick(self.desk, instant + timedelta(minutes=1))
        self.assertLessEqual(self.desk.db.execute("SELECT COUNT(*) FROM jobs WHERE kind='routine5652'").fetchone()[0], 1)   # pas de passage rejoué

    def test_HO17_reprise_priorisee_espacee_et_regroupee(self):
        self.activer()
        self.a(2026, 11, 9, 19)
        h.surveiller(self.desk)
        automatismes = [self.suspendre('monitor_matter', {'matter': 'DOS-%03d' % i}) for i in range(8)]
        demande = self.suspendre('assistant_answer', {'question': 'Fictive'}, origin='user')
        double = self.suspendre('monitor_matter', {'matter': 'DOS-doublon'})
        self.desk.db.execute("UPDATE jobs SET args=(SELECT args FROM jobs WHERE id=?) WHERE id=?", (automatismes[0], double))
        self.desk.db.commit()
        fin = self.a(2026, 11, 10, 7)
        h.surveiller(self.desk)
        lignes = {r['id']: r for r in self.desk.db.execute('SELECT id,status,not_before FROM jobs')}
        self.assertEqual(lignes[demande]['not_before'], None)                    # l'avocat d'abord, sans délai
        immediats = [j for j in automatismes if not lignes[j]['not_before']]
        self.assertEqual(len(immediats), h.REPRISE_IMMEDIATE)
        espaces = sorted(lignes[j]['not_before'] for j in automatismes if lignes[j]['not_before'])
        self.assertEqual(len(set(espaces)), 3)                                   # pas de rafale
        self.assertEqual(espaces[0], (fin + timedelta(seconds=h.REPRISE_ESPACEMENT)).isoformat())
        self.assertEqual(lignes[double]['status'], 'cancelled')                  # le même travail, une seule fois

    def test_HO17_reprise_du_matin_sans_recours_au_secours_externe(self):
        """Le secours externe se déclenche après une longue attente de file : une nuit de pause n'en est pas une."""
        from agent import secours564
        jid = self.suspendre('assistant_answer', {'question': 'Fictive'}, origin='user', created=(datetime.now(timezone.utc) - timedelta(hours=13)).isoformat(),
                             debut=self.horloge.instant - timedelta(hours=13))
        arret5643.resume(self.desk)
        vus = []
        with patch.object(Desk, 'perform', lambda *a: vus.append(secours564.waited()) or {'message': 'Réponse fictive', 'sans_controle': True}):
            self.assertTrue(self.desk.work_once())
        self.assertEqual(self.statut(jid)['status'], 'done')
        self.assertLess(vus[0], 120)

    def test_HO17_passages_periodiques_non_rejoues(self):
        self.activer()
        self.a(2026, 11, 9, 19)
        with patch.object(operations, 'datetime', wraps=datetime):
            for _ in range(3):
                operations.automation_tick(self.desk)
        creés = {r[0] for r in self.desk.db.execute('SELECT DISTINCT kind FROM jobs')}
        self.assertLessEqual(creés, {'health'})                                  # sans IA seulement : ni analyse, ni balayage
        self.assertLessEqual(self.desk.db.execute("SELECT COUNT(*) FROM jobs WHERE kind='health'").fetchone()[0], 1)

    def test_HO18_enregistrement_a_17_h_d_une_pause_commencee_a_16_h(self):
        self.a(2026, 11, 9, 17)
        jid = self.desk.enqueue('refresh_brief', {'matter': MID}, origin='automatic')
        apres_midi = [{'id': 'a', 'days': TOUS, 'kind': 'period', 'start': '16:00', 'end': '19:00'}]
        corps = {'revision': 0, 'enabled': True, 'timezone': 'Europe/Paris', 'windows': apres_midi}
        vue = h.apercu(self.desk, corps)
        self.assertTrue(vue['pause_immediate'])                                  # indiqué avant validation
        self.assertIn('immédiatement', vue['message'])
        self.assertIsNone(self.desk.settings(h.CLE_POLITIQUE))                   # l'aperçu n'écrit rien
        with self.assertRaisesRegex(Stop, 'confirmation_pause_immediate_requise'):
            h.enregistrer(self.desk, corps, 'admin@example.test')
        self.assertEqual(h.etat_public(self.desk)['revision'], 0)
        etat = h.enregistrer(self.desk, corps, 'admin@example.test', confirmer=True)
        self.assertTrue(etat['en_pause'])
        self.assertEqual(self.statut(jid)['status'], 'suspended')                # effet immédiat
        self.assertEqual(arret5643.reason_to_stop(self.desk.c['state_dir']), 'ia_en_pause_horaire')
        retrait = h.enregistrer(self.desk, {**corps, 'revision': 1, 'windows': []}, 'admin@example.test')
        self.assertFalse(retrait['en_pause'])
        self.assertEqual(self.statut(jid)['status'], 'pending')                  # plage supprimée : reprise

    def test_HO18_suppression_d_une_plage_active_avec_un_autre_motif(self):
        self.activer()
        self.a(2026, 11, 9, 19)
        h.surveiller(self.desk)
        jid = self.suspendre()
        pause_all(self.desk, True)
        self.activer([])
        self.assertEqual(self.statut(jid)['status'], 'suspended')                # la pause manuelle reste
        self.assertEqual(h.evaluer(self.desk)['motifs'], ['pause_manuelle'])

    def test_HO19_une_seule_transition_entre_plusieurs_travailleurs(self):
        self.activer()
        self.a(2026, 11, 9, 19)
        with patch.object(pause5629, 'check_enqueue'):
            suspendu = self.desk.enqueue('refresh_brief', {'matter': MID}, origin='automatic')
        h.surveiller(self.desk)
        self.a(2026, 11, 10, 7)
        resultats, erreurs, depart = [], [], threading.Barrier(4)

        def travailleur():
            d = Desk(self.f.c)
            try:
                depart.wait(5)
                resultats.append(h.surveiller(d))
            except BaseException as error:   # noqa: BLE001
                erreurs.append(error)
            finally:
                d.db.close()
        fils = [threading.Thread(target=travailleur) for _ in range(4)]
        [f.start() for f in fils]
        [f.join(30) for f in fils]
        self.assertEqual(erreurs, [])
        pris = [r for r in resultats if r]
        self.assertEqual(len(pris), 1)
        self.assertEqual(pris[0]['repris'], 1)
        self.assertEqual([t['vers'] for t in self.transitions()], ['pause', 'libre'])
        self.assertEqual(self.statut(suspendu)['status'], 'pending')

    def test_HO19_ancienne_revision_refusee_et_rien_n_est_ecrase(self):
        self.activer()
        ancien = {'revision': 0, 'enabled': False, 'timezone': 'Europe/Paris', 'windows': []}
        with self.assertRaisesRegex(Stop, 'revision_perimee'):
            h.enregistrer(self.desk, ancien, 'admin@example.test')
        etat = h.etat_public(self.desk)
        self.assertEqual((etat['revision'], etat['politique']['enabled'], len(etat['politique']['windows'])), (1, True, 2))

    def test_HO19_annulation_concurrente_reste_terminale(self):
        self.activer()
        self.a(2026, 11, 9, 19)
        h.surveiller(self.desk)
        jid = self.suspendre()
        self.a(2026, 11, 10, 7)
        reel = arret5643._held_by_schedule

        def annule_pendant_la_reprise(desk, ligne, args=None, now=None):
            desk.cancel_job(jid)
            return reel(desk, ligne, args, now)
        with patch.object(arret5643, '_held_by_schedule', annule_pendant_la_reprise):
            arret5643.resume(self.desk)
        self.assertEqual(self.statut(jid)['status'], 'cancelled')

    def illisible(self, valeur='{pas du json'):
        self.desk.db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)', (h.CLE_POLITIQUE, valeur))
        self.desk.db.commit()

    def test_HO20_configuration_illisible_bloque_et_affiche_un_incident(self):
        self.activer()
        self.illisible()
        self.assertEqual(arret5643.reason_to_stop(self.desk.c['state_dir']), 'horaires_ia_illisibles')
        with self.assertRaisesRegex(Stop, 'horaires_ia_illisibles'):
            arret5643.before_model_call({'state_dir': self.desk.c['state_dir']})
        self.assertEqual(pause5629.blocked_for_config(self.desk.c), 'horaires_ia_illisibles')
        decision = h.evaluer(self.desk)
        self.assertEqual((decision['autorise'], decision['motifs'], decision['incident']), (False, ['securite'], 'politique_illisible'))
        etat = h.etat_public(self.desk)
        self.assertTrue(etat['synthese'].startswith('Incident'))
        self.assertEqual(h.surveiller(self.desk)['vers'], 'incident')
        self.assertEqual(self.desk.db.execute("SELECT COUNT(*) FROM live_events_v430 WHERE kind='blocked' AND dedupe LIKE 'horaires5689-%'").fetchone()[0], 1)
        self.assertIsNone(h.surveiller(self.desk))                               # une seule fois

    def test_HO20_formes_illisibles_et_aucun_mode_ouvert(self):
        for valeur in ('{', 'null', '[]', '{"enabled": true}', json.dumps({**politique(NUIT), 'windows': [{'id': 'x'}]}), json.dumps({**politique(NUIT), 'revision': 'a'})):
            self.illisible(valeur)
            self.assertEqual(arret5643.reason_to_stop(self.desk.c['state_dir']), 'horaires_ia_illisibles', valeur)

    def test_HO20_services_sans_ia_preserves(self):
        self.illisible()
        sauvegarde = self.desk.enqueue('sauvegarde5688', {'origine': 'essai'}, origin='automatic')
        self.assertEqual(self.statut(sauvegarde)['status'], 'pending')
        pause5629.check_running(self.desk, self.desk.db.execute('SELECT * FROM jobs WHERE id=?', (sauvegarde,)).fetchone(), {})
        travail = self.desk.enqueue('assistant_answer', {'question': 'Fictive'}, origin='user')
        self.assertEqual(self.statut(travail)['status'], 'pending')              # la demande de l'avocat est conservée
        self.assertTrue(self.desk.work_once())
        self.assertTrue(self.desk.work_once())
        self.assertEqual(self.statut(travail)['status'], 'suspended')
        self.assertEqual(self.statut(travail)['attempts'], 0)
        self.activer(actif=False)                                                # réglage réparé : reprise
        self.assertEqual(self.statut(travail)['status'], 'pending')

    def test_HO20_fuseau_introuvable_incident_prevu(self):
        self.activer()
        self.a(2026, 11, 9, 12)                                                  # hors plage : un calcul ouvert laisserait passer
        with patch.object(h, 'ZoneInfo', side_effect=h.ZoneInfoNotFoundError('fuseau absent')):
            self.assertEqual(arret5643.reason_to_stop(self.desk.c['state_dir']), 'horaires_ia_illisibles')
            decision = h.evaluer(self.desk)
            self.assertEqual((decision['autorise'], decision['incident']), (False, 'fuseau_introuvable'))
            self.assertIn('fuseau', h.etat_public(self.desk)['synthese'])
            with self.assertRaisesRegex(Stop, 'horaires_fuseau_inconnu'):
                h.valider(politique(NUIT))
        self.assertEqual(arret5643.reason_to_stop(self.desk.c['state_dir']), '')

    def test_HO20_parametres_affichent_l_incident_si_la_base_des_fuseaux_manque(self):
        self.activer()
        with patch.object(h, 'ZoneInfo', side_effect=h.ZoneInfoNotFoundError('fuseau absent')), patch.object(hw, 'available_timezones', return_value=frozenset()), \
                patch.object(h, 'available_timezones', return_value=frozenset()):
            page = hw.section_html(self.desk, '/agent-courriel', 'admin@example.test', 'administrateur', self.horloge.instant)
            self.assertIn('Incident', page)
            self.assertIn('fuseau horaire du cabinet est introuvable', page)
            self.assertIn('Les nouveaux appels à l’IA sont bloqués', page)
            with self.assertRaisesRegex(Stop, 'horaires_fuseau_inconnu'):        # et le réglage ne peut pas être enregistré à l'aveugle
                h.enregistrer(self.desk, {'revision': 1, 'enabled': True, 'timezone': 'Europe/Paris', 'windows': NUIT}, 'admin@example.test')
            self.assertFalse(h.lire_horaire({h.CLE_POLITIQUE: json.dumps(politique(NUIT))}, self.horloge.instant)['etat'] == 'libre')

    def test_HO20_fuseau_absent_mais_planning_desactive_rien_ne_bloque(self):
        self.activer(actif=False)
        with patch.object(h, 'ZoneInfo', side_effect=h.ZoneInfoNotFoundError('fuseau absent')):
            self.assertEqual(arret5643.reason_to_stop(self.desk.c['state_dir']), '')

    def test_HO20_instance_non_configuree(self):
        vide = self.root / 'vide'
        vide.mkdir()
        self.assertEqual(arret5643.reason_to_stop(str(vide)), '')
        self.assertEqual(h.photo(str(vide)), {})

    def test_HO20_table_des_reglages_illisible(self):
        faux = self.root / 'abime'
        faux.mkdir()
        (faux / 'desk.sqlite3').write_bytes(b'pas une base')
        with self.assertRaisesRegex(Stop, 'controle_pause_indisponible'):
            arret5643.reason_to_stop(str(faux))

    def test_HO23_sans_navigateur_ni_modele_ni_tache_ni_notification_repetee(self):
        self.activer()
        self.a(2026, 11, 9, 19)
        avant = self.desk.db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0]
        with patch.object(Model, 'complete', side_effect=AssertionError('modèle appelé')), patch.object(Model, 'ask', side_effect=AssertionError('modèle appelé')):
            self.assertEqual(h.surveiller(self.desk)['vers'], 'pause')           # aucune page ouverte : la boucle du serveur suffit
            ecritures = self.desk.db.total_changes
            for seconde in range(1, 50):
                self.horloge.instant += timedelta(seconds=seconde)
                self.assertIsNone(h.surveiller(self.desk))
        self.assertEqual(self.desk.db.total_changes, ecritures)                  # surveillance sans écriture
        self.assertEqual(self.desk.db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0], avant)
        self.assertEqual(self.desk.db.execute("SELECT COUNT(*) FROM live_events_v430 WHERE dedupe LIKE 'horaires5689-%'").fetchone()[0], 1)
        self.assertEqual(len(self.transitions()), 1)

    def test_HO23_boucle_des_automatismes_raccordee(self):
        self.activer()
        self.a(2026, 11, 9, 19)
        jid = self.desk.enqueue('assistant_answer', {'question': 'Fictive'}, origin='user')
        operations.automation_tick(self.desk)
        self.assertEqual(self.transitions()[-1]['vers'], 'pause')
        self.assertEqual(self.statut(jid)['status'], 'suspended')

    def test_HO23_superviseur_sans_planning_n_ouvre_rien(self):
        self.assertIsNone(h.superviser(self.desk.c))
        self.assertFalse(self.desk.db.execute("SELECT 1 FROM settings WHERE key LIKE 'horaires_ia5689:%'").fetchone())
        self.activer()
        self.a(2026, 11, 9, 19)
        ouvert = h.superviser(self.desk.c)
        self.addCleanup(ouvert.db.close)
        self.assertEqual([t['vers'] for t in self.transitions()], ['pause'])

    def test_HO24_desactive_a_la_migration_et_migration_idempotente(self):
        chemin = Path(self.desk.c['state_dir']) / 'desk.sqlite3'
        base_seule = sqlite3.connect(str(chemin))
        colonnes = {r[1] for r in base_seule.execute('PRAGMA table_info(jobs)')}
        base_seule.close()
        self.assertTrue({'pause_debut', 'pause_secondes'} <= colonnes)
        h.ensure_schema(self.desk)                                               # sans effet au second passage
        etat = h.etat_public(self.desk)
        self.assertEqual((etat['politique']['enabled'], etat['revision'], etat['politique']['windows']), (False, 0, []))
        self.assertIsNone(self.desk.settings(h.CLE_POLITIQUE))                   # rien n'est écrit par la migration
        self.a(2026, 11, 9, 23)
        self.assertEqual(arret5643.reason_to_stop(self.desk.c['state_dir']), '')
        self.assertTrue(h.evaluer(self.desk)['autorise'])
        self.assertIsNone(h.surveiller(self.desk))
        self.assertFalse(self.desk.db.execute("SELECT 1 FROM settings WHERE key=?", (h.CLE_ETAT,)).fetchone())
        jid = self.desk.enqueue('refresh_brief', {'matter': MID}, origin='automatic')
        self.assertEqual(self.statut(jid)['status'], 'pending')

    def test_HO24_desactiver_conserve_pause_manuelle_travaux_autorisations_et_journaux(self):
        self.activer()
        self.a(2026, 11, 9, 19)
        h.surveiller(self.desk)
        pause_all(self.desk, True)
        jid = self.suspendre()
        journal = self.desk.db.execute('SELECT COUNT(*) FROM audit').fetchone()[0]
        self.desk.setting('autorisations-fictives', {'a': 1})
        self.activer(actif=False)
        self.assertTrue(self.desk.settings('missions567:pause', False))
        self.assertEqual(self.statut(jid)['status'], 'suspended')
        self.assertEqual(self.desk.settings('autorisations-fictives'), {'a': 1})
        self.assertGreater(self.desk.db.execute('SELECT COUNT(*) FROM audit').fetchone()[0], journal)
        self.assertEqual(h.evaluer(self.desk)['motifs'], ['pause_manuelle'])
        pause_all(self.desk, False)
        self.assertEqual(self.statut(jid)['status'], 'pending')

    def test_mise_a_jour_garde_sa_propre_logique_pendant_une_plage(self):
        from agent import maj5661
        self.activer()
        self.a(2026, 11, 9, 19)
        self.assertTrue(maj5661.pause_before_update(self.desk, '9.9.9', sleep=lambda s: None))   # la plage n'est pas une pause de l'avocat
        self.assertTrue(self.desk.settings('missions567:pause', False))
        self.assertTrue(maj5661.resume_after_update(self.desk, now=datetime.now(timezone.utc) + timedelta(days=1)))
        self.assertFalse(self.desk.settings('missions567:pause', False))
        self.assertTrue(h.etat_public(self.desk)['politique']['enabled'])

    def test_retention_et_export_des_reponses_tardives(self):
        self.activer()
        self.a(2026, 11, 9, 12)
        h.garder_reponse_tardive({'state_dir': self.desk.c['state_dir'], 'job_id': 1, 'matter': MID}, 'ia_en_pause_horaire', {'texte': 'fictif'})
        self.assertEqual(h.purger(self.desk), 0)
        self.a(2026, 12, 20, 12)
        self.assertEqual(h.purger(self.desk), 1)
        self.assertIn('reponses_tardives5689', {t[2] for t in export5688.TABLES})


class Droits(Base):
    ORIGIN = 'https://cabinet.example.test'
    AUTH = {'origin': ORIGIN, 'csrf': 'jeton-csrf-fictif'}

    def env(self, corps=None, role='administrateur', compte='admin@example.test', csrf='jeton-csrf-fictif', origine=ORIGIN, methode='POST'):
        donnees = json.dumps(corps if corps is not None else {}).encode()
        return {'REQUEST_METHOD': methode, 'wsgi.input': io.BytesIO(donnees), 'CONTENT_LENGTH': str(len(donnees)), 'CONTENT_TYPE': 'application/json',
                'HTTP_ORIGIN': origine, 'HTTP_X_CSRF_TOKEN': csrf, 'HTTP_X_AXIORHUB_USER': compte, 'HTTP_X_AXIORHUB_ROLE': role}

    def appel(self, route, corps=None, methode='POST', **kw):
        reponse = hw.api(self.env(corps, methode=methode, **kw), self.desk, self.AUTH, '/agent-courriel', 'm5689/' + route, {}, methode)
        statut, corps_json = reponse['status'], json.loads(b''.join(reponse['body']) if isinstance(reponse['body'], (list, tuple)) else reponse['body'])
        return statut, corps_json

    def corps(self, **extra):
        return {'revision': 0, 'enabled': True, 'timezone': 'Europe/Paris', 'windows': NUIT, **extra}

    def test_HO21_seul_le_role_autorise_modifie_le_planning(self):
        for role, compte in (('assistant', 'assistant@example.test'), ('avocat', 'avocat@example.test')):
            statut, sortie = self.appel('horaires', self.corps(), role=role, compte=compte)
            self.assertTrue(statut.startswith('403'), (role, statut))
            self.assertEqual(sortie['error'], 'role_insuffisant')
        self.assertIsNone(self.desk.settings(h.CLE_POLITIQUE))
        self.assertFalse(hw.allowed('assistant', 'horaires'))
        self.assertFalse(hw.allowed('avocat', 'horaires/inconnue'))
        self.assertFalse(standalone_auth.allowed('assistant', 'POST', '/api440/m5689/horaires'))
        self.assertTrue(standalone_auth.allowed('assistant', 'GET', '/api440/m5689/horaires'))
        self.assertEqual(membres5650.category('POST', '/api440/m5689/horaires'), 'reglages')
        self.assertIsNone(membres5650.category('GET', '/api440/m5689/horaires'))
        self.assertFalse(membres5650.allowed(self.desk.c['state_dir'], 'avocat@example.test', 'avocat', 'POST', '/api440/m5689/horaires'))
        membres5650.set_rights(self.desk.c['state_dir'], 'admin@example.test', 'avocat@example.test', 'avocat', {'reglages': True})
        self.assertTrue(membres5650.allowed(self.desk.c['state_dir'], 'avocat@example.test', 'avocat', 'POST', '/api440/m5689/horaires'))
        statut, sortie = self.appel('horaires', self.corps(), role='avocat', compte='avocat@example.test')
        self.assertTrue(statut.startswith('200'), sortie)
        self.assertEqual(sortie['revision'], 1)

    def test_HO21_controle_csrf_et_origine(self):
        for kw in ({'csrf': 'mauvais-jeton'}, {'origine': 'https://autre.example.test'}):
            statut, sortie = self.appel('horaires', self.corps(), **kw)
            self.assertFalse(statut.startswith('200'), kw)
            self.assertEqual(sortie['error'], 'origine_ou_csrf_refuse')
        self.assertIsNone(self.desk.settings(h.CLE_POLITIQUE))
        for route in ('horaires/apercu', 'horaires/derogation', 'horaires/derogation/retirer'):
            self.assertEqual(self.appel(route, self.corps(), csrf='mauvais-jeton')[1]['error'], 'origine_ou_csrf_refuse')

    def test_HO21_revision_perimee_409_et_lecture_pour_tous(self):
        self.assertTrue(self.appel('horaires', self.corps())[0].startswith('200'))
        statut, sortie = self.appel('horaires', self.corps())
        self.assertTrue(statut.startswith('409'))
        self.assertEqual(sortie['error'], 'revision_perimee')
        self.assertIn('rien n’a été écrasé', sortie['message'])
        statut, lecture = self.appel('horaires', methode='GET', role='assistant', compte='assistant@example.test')
        self.assertTrue(statut.startswith('200'))
        self.assertEqual((lecture['revision'], lecture['fuseau']), (1, 'Europe/Paris'))

    def test_HO21_confirmation_pause_immediate_en_409(self):
        self.a(2026, 11, 9, 20)
        statut, sortie = self.appel('horaires', self.corps())
        self.assertTrue(statut.startswith('409'))
        self.assertEqual(sortie['error'], 'confirmation_pause_immediate_requise')
        self.assertTrue(self.appel('horaires', self.corps(confirmer_pause_immediate=True))[1]['en_pause'])

    def test_HO21_erreurs_de_validation_sans_ecriture(self):
        statut, sortie = self.appel('horaires', self.corps(timezone='Europe/Atlantide'))
        self.assertTrue(statut.startswith('400'))
        self.assertEqual(sortie['error'], 'horaires_fuseau_inconnu')
        self.assertIn('Fuseau horaire inconnu', sortie['message'])
        self.assertIsNone(self.desk.settings(h.CLE_POLITIQUE))

    def test_HO21_journal_sans_contenu_confidentiel(self):
        self.a(2026, 11, 9, 19)
        self.appel('horaires', self.corps(confirmer_pause_immediate=True))
        self.appel('horaires/derogation', {'perimetre': 'dossier', 'dossier': MID, 'cle': 'cle-fictive'})
        lignes = self.desk.db.execute("SELECT action,data FROM audit WHERE action LIKE 'horaires_ia5689%'").fetchall()
        self.assertGreaterEqual(len(lignes), 3)
        for ligne in lignes:
            donnees = json.loads(ligne['data'])
            self.assertLessEqual(set(donnees), {'revision', 'active', 'fuseau', 'plages', 'par', 'auteur', 'de', 'vers', 'incident', 'suspendus', 'arrets_demandes', 'en_cours',
                                                'id', 'fin', 'perimetre', 'dossier', 'repris', 'reliquat'}, ligne['action'])
            self.assertNotIn('windows', ligne['data'])
            self.assertNotIn('question', ligne['data'])

    def test_HO21_derogation_droits_et_dossier_reserve(self):
        self.a(2026, 11, 9, 19)
        self.appel('horaires', self.corps(confirmer_pause_immediate=True))
        self.assertTrue(self.appel('horaires/derogation', {}, role='assistant', compte='assistant@example.test')[0].startswith('403'))
        membres5650.set_rights(self.desk.c['state_dir'], 'admin@example.test', 'avocat@example.test', 'avocat', {'reglages': True})
        membres5650.set_reserve(self.desk.c['state_dir'], MID, ['autre@example.test'])
        statut, sortie = self.appel('horaires/derogation', {'perimetre': 'dossier', 'dossier': MID}, role='avocat', compte='avocat@example.test')
        self.assertEqual(sortie['error'], 'dossier_absent')
        self.assertTrue(self.appel('horaires/derogation', {'perimetre': 'demandes'})[0].startswith('200'))
        self.assertIsNotNone(self.appel('horaires/derogation/retirer', {})[1])
        self.assertIsNone(h.etat_public(self.desk)['derogation'])

    def test_HO21_route_inconnue(self):
        self.assertTrue(self.appel('horaires/autre', {})[0].startswith('404'))


class Presentation(Base):
    def html(self, role='administrateur', compte='admin@example.test'):
        return hw.section_html(self.desk, '/agent-courriel', compte, role, self.horloge.instant)

    def test_HO22_ecran_complet_au_clavier_en_24_h(self):
        self.activer()
        self.a(2026, 11, 9, 12)
        page = self.html()
        for texte in ('Programmer les pauses de l’IA', 'Fuseau horaire du cabinet', 'Pause chaque jour de 18 h à 7 h', 'Tous les jours', 'Jours ouvrés', 'Personnaliser',
                      'Ajouter une plage', 'Supprimer cette plage', 'Enregistrer', 'Journée entière', 'format 24 h', 'La fin tombe le lendemain'):
            self.assertIn(texte, page)
        self.assertIn('role="switch"', page)
        self.assertIn('role="status"', page)
        self.assertIn('role="alert"', page)
        self.assertNotIn('type="time"', page)                                    # la saisie reste au format 24 h quel que soit le navigateur
        self.assertNotIn('tabindex="-1"', page)
        self.assertNotIn('onclick', page)                                        # aucun script en ligne
        self.assertNotIn('<script', page)
        self.assertEqual(page.count('<button'), page.count('</button>'))
        self.assertEqual(page.count('<fieldset'), 3)                             # deux plages et le modèle
        self.assertRegex(page, r'<legend>Plage <span data-numero>1</span></legend>')
        self.assertIn('value="18:00"', page)
        self.assertIn('value="00:00"', page)

    def test_HO22_synthese_prochain_changement_et_etat_en_mots(self):
        self.activer()
        self.a(2026, 11, 9, 12)
        self.assertIn('Prochaine pause aujourd’hui à 18 h', self.html())
        self.assertIn('Prochain changement : aujourd’hui à 18:00 (Europe/Paris).', self.html())
        self.a(2026, 11, 9, 19)
        self.assertIn('IA en pause programmée jusqu’à demain 7 h', self.html())
        self.a(2026, 11, 10, 1)
        self.assertIn('IA en pause programmée jusqu’à aujourd’hui 7 h', self.html())
        self.assertIn('Autoriser exceptionnellement pendant 30 minutes', self.html())
        self.a(2026, 11, 9, 19)
        h.accorder_derogation(self.desk, 'admin@example.test')
        page = self.html()
        self.assertIn('Dérogation en cours', page)
        self.assertIn('Périmètre : Demandes de l’avocat', page)
        self.assertIn('19:30', page)

    def test_HO22_lendemain_et_journee_entiere_indiques(self):
        self.assertTrue(hw.lendemain({'kind': 'period', 'start': '18:00', 'end': '07:00'}))
        self.assertTrue(hw.lendemain({'kind': 'period', 'start': '18:00', 'end': '00:00'}))
        self.assertFalse(hw.lendemain({'kind': 'period', 'start': '00:00', 'end': '07:00'}))
        self.assertFalse(hw.lendemain({'kind': 'full_day'}))
        self.assertEqual((hw.choix_jours(TOUS), hw.choix_jours([1, 2, 3, 4, 5]), hw.choix_jours([5])), ('tous', 'ouvres', 'perso'))
        self.activer([{'id': 'f', 'days': [1, 2, 3, 4, 5], 'kind': 'full_day'}])
        page = self.html()
        self.assertRegex(page, r'<option value="ouvres" selected>')
        self.assertRegex(page, r'<option value="full_day" selected>')

    def test_HO22_incident_et_lecture_seule_sans_couleur_seule(self):
        self.desk.db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)', (h.CLE_POLITIQUE, '{illisible'))
        self.desk.db.commit()
        page = self.html()
        self.assertIn('<strong>Incident :</strong>', page)
        self.assertIn('les services sans IA continuent', page)
        self.desk.db.execute('DELETE FROM settings WHERE key=?', (h.CLE_POLITIQUE,))
        self.desk.db.commit()
        self.activer()
        page = self.html('assistant', 'assistant@example.test')
        self.assertIn('Consultation seule', page)
        self.assertNotIn('id="h89-enregistrer"', page)
        self.assertNotIn('data-supprimer', page)
        self.assertNotIn('<template', page)
        self.assertIn('disabled', page)

    def test_HO22_echappement(self):
        self.activer([{'id': '"><img src=x>', 'days': TOUS, 'kind': 'period', 'start': '18:00', 'end': '07:00'}])
        self.assertNotIn('<img src=x>', self.html())

    def test_HO22_rubrique_des_parametres(self):
        from agent import parametres5621 as p21
        self.assertIn('horaires', dict((k, l) for k, l, _ in p21.RUBRIQUES))
        famille = next(f for f, cles in p21.FAMILIES if 'horaires' in cles)
        self.assertEqual(famille, 'IA et automatisations')
        self.assertEqual(p21.rubrique_for('/parametres', {'rubrique': 'horaires'}), 'horaires')

    def test_HO22_logique_du_script(self):
        noeud = shutil.which('node')
        if not noeud:
            self.skipTest('node absent : logique du script non exécutée')
        code = ("const l=require(%s);console.log(JSON.stringify({"
                "a:l.surLendemain('18:00','07:00'),b:l.surLendemain('00:00','07:00'),c:l.surLendemain('18:00','24:00'),"
                "d:l.erreurPlage({days:[],kind:'period',start:'18:00',end:'07:00'}),e:l.erreurPlage({days:[1],kind:'period',start:'7h',end:'07:00'}),"
                "f:l.erreurPlage({days:[1],kind:'period',start:'18:00',end:'18:00'}),g:l.erreurPlage({days:[1],kind:'full_day'}),h:l.raccourci()}))"
                % json.dumps(str(ROOT / 'agent' / 'static' / 'v5689-horaires.js')))
        sortie = json.loads(subprocess.run([noeud, '-e', code], capture_output=True, text=True, encoding='utf-8', timeout=30, check=True).stdout)
        self.assertEqual((sortie['a'], sortie['b'], sortie['c']), (True, False, True))
        self.assertIn('Choisissez au moins un jour', sortie['d'])
        self.assertIn('format 24 h', sortie['e'])
        self.assertIn('Journée entière', sortie['f'])
        self.assertEqual(sortie['g'], '')
        self.assertEqual(h.valider(politique(sortie['h']))['windows'], NUIT)     # le raccourci est accepté tel quel par le serveur
        self.assertFalse(calcule(sortie['h'], paris(2026, 11, 10, 7))['pause'])

    def test_HO22_script_declare(self):
        from agent.web440 import STATIC
        self.assertEqual(STATIC['/static/v5689-horaires.js'], 'text/javascript')
        self.assertTrue((ROOT / 'agent' / 'static' / 'v5689-horaires.js').is_file())

    def test_HO22_bandeau_de_l_accueil_en_mots(self):
        from agent import ia5672
        self.assertEqual(ia5672.banner_html(self.desk, '/agent-courriel'), '')   # sans planning : rien
        self.activer()
        self.a(2026, 11, 9, 12)
        self.assertEqual(ia5672.banner_html(self.desk, '/agent-courriel'), '')
        self.a(2026, 11, 9, 19)
        self.assertIn('IA en pause programmée jusqu’à demain 7 h', ia5672.banner_html(self.desk, '/agent-courriel'))
        self.desk.db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)', (h.CLE_POLITIQUE, '{illisible'))
        self.desk.db.commit()
        bandeau = ia5672.banner_html(self.desk, '/agent-courriel')
        self.assertIn('role="alert"', bandeau)
        self.assertIn('Incident', bandeau)

    def test_diagnostic_montre_l_incident(self):
        from agent import blocage5660
        self.desk.db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)', (h.CLE_POLITIQUE, '{illisible'))
        self.desk.db.commit()
        rapport = blocage5660.diagnose(self.desk.db, self.desk.c['state_dir'], self.horloge.instant)
        self.assertIn('horaires_illisibles', json.dumps(rapport, ensure_ascii=False))


class Application(unittest.TestCase):
    """Toute la pile web : authentification, droits, ``web5689``, CSRF, page de Paramètres. Compte administrateur fictif."""
    ORIGIN = 'https://cabinet.example.test'

    def setUp(self):
        from agent.common import private_json
        from agent.web import App
        self.f = fixtures.EngineTests(methodName='test_observation_has_no_mail_write')
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.config = self.f.base / 'config.json'
        private_json(self.config, self.f.c)
        self.auth = self.f.base / 'auth.json'
        sel = 'aa' * 16
        self.secret = 'password-test-only-long'
        private_json(self.auth, {'username': 'admin', 'salt': sel, 'hash': hashlib.scrypt(self.secret.encode(), salt=bytes.fromhex(sel), n=16384, r=8, p=1).hex(),
                                 'origin': self.ORIGIN, 'prefix': '/agent-courriel', 'csrf': 'test-csrf'})
        self.app = App(str(self.config), str(self.auth))
        self.desk = Desk(self.f.c)
        self.addCleanup(self.desk.db.close)

    def requete(self, chemin, methode='GET', corps=None, requete='', csrf='test-csrf'):
        donnees = json.dumps(corps or {}).encode()
        env = {'REQUEST_METHOD': methode, 'PATH_INFO': chemin, 'QUERY_STRING': requete, 'HTTP_HOST': 'cabinet.example.test', 'HTTP_X_FORWARDED_PROTO': 'https', 'wsgi.url_scheme': 'http',
               'wsgi.input': io.BytesIO(donnees), 'CONTENT_LENGTH': str(len(donnees)), 'CONTENT_TYPE': 'application/json',
               'HTTP_AUTHORIZATION': 'Basic ' + base64.b64encode(('admin:' + self.secret).encode()).decode(), 'HTTP_ORIGIN': self.ORIGIN, 'HTTP_X_CSRF_TOKEN': csrf}
        sortie = {}

        def debut(statut, entetes):
            sortie.update(status=statut, headers=dict(entetes))
        reponse = self.app(env, debut)
        sortie['body'] = b''.join(reponse)
        return sortie

    def test_HO21_ecriture_par_la_pile_complete(self):
        corps = {'revision': 0, 'enabled': False, 'timezone': 'Europe/Paris', 'windows': NUIT}
        self.assertEqual(json.loads(self.requete('/api440/m5689/horaires', 'POST', corps, csrf='mauvais')['body'])['error'], 'origine_ou_csrf_refuse')
        reponse = self.requete('/api440/m5689/horaires', 'POST', corps)
        self.assertTrue(reponse['status'].startswith('200'), reponse['body'])
        self.assertEqual(json.loads(self.requete('/api440/m5689/horaires')['body'])['revision'], 1)
        self.assertTrue(self.requete('/api440/m5689/horaires', 'POST', corps)['status'].startswith('409'))
        self.assertFalse(h.etat_public(self.desk)['politique']['enabled'])

    def test_HO22_page_de_parametres_rendue(self):
        page = self.requete('/parametres', requete='rubrique=horaires')
        texte = page['body'].decode()
        self.assertTrue(page['status'].startswith('200'))
        self.assertIn('id="h89-horaires"', texte)
        self.assertIn('IA et automatisations', texte)
        self.assertRegex(texte, r'<script defer src="[^"]*v5689-horaires[.]js')
        self.assertIn('Programmation désactivée', texte)
        self.assertNotIn('checked> Programmer les pauses', texte)                # désactivé tant que le cabinet ne l'active pas
        self.assertTrue(self.requete('/static/v5689-horaires.js')['status'].startswith('200'))


class ParcoursNuit(Base):
    def test_HO23_nuit_complete_avec_les_vrais_travailleurs_de_file(self):
        """Un travail mis en file l'après-midi, la nuit, la reprise : tout passe par la file réelle, avec un faux producteur."""
        self.activer()
        self.a(2026, 11, 9, 17)
        jid = self.desk.enqueue('assistant_answer', {'question': 'Fictive'}, origin='user')
        self.a(2026, 11, 9, 18)
        self.assertTrue(self.desk.work_once())                                   # la boucle n'a pas encore vu la transition
        self.assertEqual(self.statut(jid)['status'], 'suspended')
        self.assertEqual(self.statut(jid)['attempts'], 0)
        self.assertFalse(self.desk.work_once())
        self.a(2026, 11, 10, 7)
        operations.automation_tick(self.desk)
        self.assertEqual(self.statut(jid)['status'], 'pending')
        with patch.object(Desk, 'perform', lambda *a: {'message': 'Réponse fictive', 'sans_controle': True}):
            self.assertTrue(self.desk.work_once())
        self.assertEqual(self.statut(jid)['status'], 'done')
        self.assertEqual(self.statut(jid)['attempts'], 1)


if __name__ == '__main__':
    unittest.main()
