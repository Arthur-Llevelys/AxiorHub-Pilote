"""5.6.96, lot accueil : bandeau (F01, F02, I01), adaptateur météo OpenWeather (F03, F05), carte (F04), ordre d'Aujourd'hui (I01), fenêtre flottante (I02).

Données fictives (DURAND, MARTIN, ZORVAL, DOS-001…, ville Valbrune, coordonnées fictives, prénoms Claire et Camille, example.test). Aucun appel réseau :
le fournisseur météo est un faux client qui enregistre chaque adresse demandée ; aucune clé réelle (clé de banc fictive). Aucun modèle : tout appel
au modèle fait échouer les tests (classe ``Cabinet`` de ``test_v5695_aujourdhui``). Le rendu dans un vrai navigateur est vérifié par le banc
``scripts/parcours-navigateur.py`` (parcours P96F1 et P96F2), pas ici ; l'heure en direct par ``tests/accueil5696.cjs`` (Node).
"""
from datetime import datetime, timedelta, timezone
import io
import json
import logging
from pathlib import Path
import re
import shutil
import subprocess
import threading
import time
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit
from zoneinfo import ZoneInfo

from test_v5695_aujourdhui import MESURE, Cabinet
from agent import accueil_web5696 as acc, assistant567, data_policy5689 as dp, meteo5696 as met, tableau5688
from agent.common import Stop, read_secret
from agent.erreurs5634 import http_error

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / 'agent' / 'static'
CLE = 'cle-de-banc-fictive-0001'            # jamais une vraie clé
AUTRE_CLE = 'cle-de-banc-fictive-0002'
AVOCATE = 'avocate@example.test'
LIEU_A = {'ville': 'Valbrune', 'pays': 'FR', 'region': 'Région fictive A', 'latitude': 45.123, 'longitude': 4.567}
LIEU_B = {'ville': 'Valbrune', 'pays': 'FR', 'region': 'Région fictive B', 'latitude': 47.891, 'longitude': -1.234}


def creneaux(debut, tz, jours=5, base=5):
    """Prévision 5 jours / 3 heures fictive, à partir de ``debut`` : un créneau toutes les 3 heures (UTC), températures croissantes par jour."""
    zone = ZoneInfo(tz)
    t0 = int(debut.timestamp()) // 10800 * 10800 + 10800
    out = []
    codes = (800, 500, 803, 601, 211)
    for i in range(jours * 8):
        dt = t0 + i * 10800
        jour = (datetime.fromtimestamp(dt, timezone.utc).astimezone(zone).date() - debut.astimezone(zone).date()).days
        out.append({'dt': dt, 'main': {'temp_min': base + jour - 0.4 + (i % 2), 'temp_max': base + jour + 3.6 + (i % 2)}, 'weather': [{'id': codes[min(jour, 4)], 'main': 'x', 'description': 'x', 'icon': '01d'}]})
    return {'cod': '200', 'list': out, 'city': {'name': 'Valbrune', 'timezone': 3600}}


class FauxOpenWeather:
    """Faux client HTTP : enregistre méthode, adresse et corps ; répond selon le chemin. ``refus`` : code HTTP à renvoyer ; ``panne`` : réseau coupé."""

    def __init__(self, debut=None, tz='Europe/Paris'):
        self.appels, self.refus, self.panne, self.attente = [], None, False, None
        self.debut, self.tz = debut or datetime.now(timezone.utc), tz
        self.lieux = [{'name': 'Valbrune', 'lat': 45.1234, 'lon': 4.5678, 'country': 'FR', 'state': 'Région fictive A'},
                      {'name': 'Valbrune', 'lat': 47.8912, 'lon': -1.2345, 'country': 'FR', 'state': 'Région fictive B'}]
        self.verrou = threading.Lock()

    def request(self, method, url, data=None, headers=None, limit=None):
        with self.verrou:
            self.appels.append({'method': method, 'url': url, 'data': data, 'headers': headers})
        if self.attente is not None:
            self.attente.wait(5)
        if self.panne:
            raise Stop('connexion_http_indisponible')
        if self.refus:
            raise http_error(self.refus, method, url, b'{"cod":401,"message":"fictif"}', None)
        chemin = urlsplit(url).path
        if chemin == '/geo/1.0/direct':
            return json.dumps(self.lieux).encode()
        return json.dumps(creneaux(self.debut, self.tz)).encode()

    def params(self, i=-1):
        return {k: v[0] for k, v in parse_qs(urlsplit(self.appels[i]['url']).query).items()}


class Base(Cabinet):
    def setUp(self):
        super().setUp()
        met.vider_cache()
        self.addCleanup(met.vider_cache)
        self.faux = FauxOpenWeather()
        p = patch.object(met, 'HTTP_FACTORY', lambda base: self.faux)
        p.start()
        self.addCleanup(p.stop)
        from agent import membres5650
        self.addCleanup(membres5650.clear)

    def regler(self, lieu=LIEU_A, actif=True):
        met.tester_cle(self.d, 'cabinet', CLE)
        met.choisir_lieu(self.d, 'cabinet', lieu)
        if actif:
            met.activer(self.d, 'cabinet', True)
        self.faux.appels.clear()

    def matin(self, tz='Europe/Paris', heure=8):
        return datetime.now(ZoneInfo(tz)).replace(hour=heure, minute=0, second=0, microsecond=0)

    def appel(self, route, method='GET', body=None, role='avocat', owner=AVOCATE, csrf='jeton', args=None, etag=''):
        raw = json.dumps(body or {}).encode()
        env = {'HTTP_X_AXIORHUB_USER': owner, 'HTTP_X_AXIORHUB_ROLE': role, 'HTTP_ORIGIN': 'https://cabinet.example.test', 'HTTP_X_CSRF_TOKEN': csrf,
               'CONTENT_LENGTH': str(len(raw)), 'CONTENT_TYPE': 'application/json', 'wsgi.input': io.BytesIO(raw), 'REQUEST_METHOD': method}
        if etag:
            env['HTTP_IF_NONE_MATCH'] = etag
        from agent import web5689
        return web5689.api(env, self.d, {'origin': 'https://cabinet.example.test', 'csrf': 'jeton'}, '/p', 'm5689/' + route, args or {}, method)

    def page(self, owner='cabinet', role='administrateur', queue='<div class="c5662-file" id="c5656-file">FILE-COMPLETE</div>'):
        from agent import cockpit530
        with patch('agent.i18n5631.role', return_value=role):
            return cockpit530.page(self.d, '/p', owner=owner, queue=queue)


# ====================================================================================== F01 / I01 : bandeau
class Bandeau(Base):
    def test_I01_seuils_de_la_salutation(self):
        attendus = {0: 'soir', 4: 'soir', 5: 'matin', 11: 'matin', 12: 'apres_midi', 17: 'apres_midi', 18: 'soir', 23: 'soir'}
        for heure, mot in attendus.items():
            self.assertEqual(acc.formule(heure), mot, heure)

    def test_I01_salutation_dans_le_fuseau_du_compte(self):
        instant = datetime.now(timezone.utc).replace(hour=10, minute=30, second=0, microsecond=0)
        self.d.setting('proactive568:profile:' + AVOCATE, {'timezone': 'America/New_York'})
        self.d.setting('proactive568:profile:camille@example.test', {'timezone': 'Asia/Tokyo'})
        ny = acc.salutation(self.d, AVOCATE, instant)
        tokyo = acc.salutation(self.d, 'camille@example.test', instant)
        self.assertEqual((ny['fuseau'], ny['formule']), ('America/New_York', 'matin' if 5 <= instant.astimezone(ZoneInfo('America/New_York')).hour < 12 else ny['formule']))
        self.assertEqual(tokyo['formule'], acc.formule(instant.astimezone(ZoneInfo('Asia/Tokyo')).hour))
        self.assertNotEqual(ny['datetime'], tokyo['datetime'])                  # même instant, deux heures locales
        self.assertIn('il est %d h %02d' % (instant.astimezone(ZoneInfo('Asia/Tokyo')).hour, 30), tokyo['texte'])
        # sans fuseau propre : celui du cabinet, puis celui de l'agenda
        self.assertEqual(acc.salutation(self.d, 'claire@example.test', instant)['fuseau'], 'Europe/Paris')
        self.d.setting('proactive568:profile:cabinet', {'timezone': 'Europe/Lisbon'})
        self.assertEqual(acc.salutation(self.d, 'claire@example.test', instant)['fuseau'], 'Europe/Lisbon')

    def test_F01_prenom_valide_echappe_et_vide(self):
        instant = self.matin()
        self.assertTrue(acc.salutation(self.d, AVOCATE, instant)['texte'].startswith('Bonjour, nous sommes le '))
        assistant567.save_profile(self.d, {'prenom': '  Claire  '}, AVOCATE)
        self.assertTrue(acc.salutation(self.d, AVOCATE, instant)['texte'].startswith('Bonjour Claire, nous sommes le '))
        for refuse in ('<b>Claire</b>', 'Claire\x07', 'C' * 41, 12):
            with self.subTest(refuse=refuse), self.assertRaises(Stop):
                assistant567.save_profile(self.d, {'prenom': refuse}, AVOCATE)
        self.assertEqual(assistant567.profile(self.d, AVOCATE)['prenom'], 'Claire')       # un refus ne change rien
        assistant567.save_profile(self.d, {'prenom': 'Camille & Co'}, AVOCATE)
        html = acc.bandeau_html(self.d, AVOCATE)
        self.assertIn('Camille &amp; Co', html)
        self.assertNotIn('Camille & Co', html)
        assistant567.save_profile(self.d, {'prenom': ''}, AVOCATE)
        self.assertNotIn('Camille', acc.bandeau_html(self.d, AVOCATE))

    def test_F01_prenom_propre_au_compte_jamais_deduit_de_l_adresse(self):
        assistant567.save_profile(self.d, {'prenom': 'Claire'}, 'cabinet')
        self.assertEqual(assistant567.profile(self.d, AVOCATE)['prenom'], '')             # pas hérité du profil du cabinet
        html = acc.bandeau_html(self.d, AVOCATE)
        self.assertNotIn('Claire', html)
        self.assertNotIn('avocate', html.lower())                                          # jamais tiré de l'adresse de connexion
        self.assertIn('Claire', acc.bandeau_html(self.d, 'cabinet'))
        assistant567.save_profile(self.d, {'tone': 'direct'}, AVOCATE)                     # une autre préférence n'emporte pas le prénom du cabinet
        self.assertEqual(assistant567.profile(self.d, AVOCATE)['prenom'], '')

    def test_F01_route_options_csrf_et_compte(self):
        refus = self.appel('accueil/options', 'POST', {'prenom': 'Claire'}, csrf='faux')
        self.assertEqual(refus['status'], '403 Forbidden')
        self.assertEqual(assistant567.profile(self.d, AVOCATE)['prenom'], '')
        out = self.appel('accueil/options', 'POST', {'prenom': 'Claire'})
        self.assertEqual(out['status'], '200 OK', out['body'])
        self.assertIn('Claire', json.loads(out['body'])['salutation'])
        self.assertEqual(assistant567.profile(self.d, AVOCATE)['prenom'], 'Claire')
        self.assertEqual(assistant567.profile(self.d, 'camille@example.test')['prenom'], '')
        mauvais = self.appel('accueil/options', 'POST', {'prenom': '<script>'})
        self.assertEqual((mauvais['status'], json.loads(mauvais['body'])['error']), ('400 Bad Request', 'prenom_invalide'))

    def test_F02_rendu_initial_sans_annonce_et_script_differe(self):
        html = self.page()
        bandeau = re.search(r'<p class="f96-bandeau".*?</p>', html, re.S).group(0)
        self.assertIn('<time datetime="', bandeau)
        self.assertNotIn('aria-live', bandeau)
        self.assertRegex(bandeau, r'il est \d{1,2} h \d{2}\.')
        self.assertIn('<script defer src="/p/static/v5696-accueil.js"></script>', html)
        self.assertIn('/static/v5696-accueil.js', __import__('agent.web440', fromlist=['STATIC']).STATIC)
        messages = json.loads(re.search(r'<script type="application/json" id="f96-messages" data-f96-messages>(.*?)</script>', html, re.S).group(1))
        self.assertEqual(messages['formule.matin'], 'Bonjour')
        self.assertIn('{heure}', messages['phrase'])
        self.assertLess(html.index('Pause de l’IA'), html.index('Confier une mission'))    # commandes conservées dans l'en-tête

    def test_F02_heure_en_direct_node(self):
        node = shutil.which('node')
        if not node:
            self.skipTest('Node absent')
        out = subprocess.run([node, str(ROOT / 'tests' / 'accueil5696.cjs')], capture_output=True, text=True, encoding='utf-8', timeout=120)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertIn('"minuit_change_la_date": true', out.stdout)


# ====================================================================================== F03 / F05 : adaptateur météo
class Meteo(Base):
    def test_F05_desactive_par_defaut_aucun_appel_ni_carte(self):
        out = met.previsions(self.d, AVOCATE)
        self.assertEqual((out['statut'], self.faux.appels), ('desactive', []))
        self.assertNotIn('data-f96-meteo', self.page(AVOCATE, 'avocat'))
        admin = self.page()
        self.assertNotIn('data-f96-meteo', admin)
        self.assertIn('f96-meteo-off', admin)                                              # l'administrateur voit où régler, sans requête
        # mutation en mémoire : sans la garde d'activation, l'appel part (la garde est bien ce qui l'empêche)
        self.regler(actif=False)
        vraie = met.configuration
        with patch.object(met, 'configuration', lambda desk: dict(vraie(desk), enabled=True)):
            met.previsions(self.d, AVOCATE)
        self.assertEqual(len(self.faux.appels), 1)

    def test_F05_activation_exige_cle_testee_et_lieu(self):
        with self.assertRaises(Stop) as refus:
            met.activer(self.d, 'cabinet', True)
        self.assertEqual(str(refus.exception), 'cle_meteo_non_testee')
        met.tester_cle(self.d, 'cabinet', CLE)
        self.assertEqual(read_secret(met.chemin_cle(self.d)), CLE)
        self.assertNotIn(CLE, met.chemin_cle(self.d).read_text(encoding='utf-8'))          # chiffrée dans le coffre
        with self.assertRaises(Stop) as refus:
            met.activer(self.d, 'cabinet', True)
        self.assertEqual(str(refus.exception), 'lieu_meteo_absent')
        met.choisir_lieu(self.d, 'cabinet', LIEU_A)
        cfg = met.activer(self.d, 'cabinet', True)
        self.assertTrue(cfg['enabled'])
        self.assertEqual((self.faux.params(0)['lat'], self.faux.params(0)['lon']), ('0', '0'))   # le test de la clé n'envoie aucune position du cabinet
        met.effacer_cle(self.d, 'cabinet')
        self.assertFalse(met.configuration(self.d)['enabled'])
        self.assertFalse(met.chemin_cle(self.d).exists())

    def test_F05_cle_erronee_ne_remplace_pas_la_cle_en_service(self):
        met.tester_cle(self.d, 'cabinet', CLE)
        self.faux.refus = 401
        with self.assertRaises(Stop) as refus:
            met.tester_cle(self.d, 'cabinet', AUTRE_CLE)
        self.assertEqual(str(refus.exception), 'cle_refusee')
        self.assertEqual(read_secret(met.chemin_cle(self.d)), CLE)
        self.assertTrue(met.configuration(self.d)['cle_testee'])
        self.faux.refus = 429
        with self.assertRaises(Stop) as refus:
            met.tester_cle(self.d, 'cabinet')
        self.assertEqual(str(refus.exception), 'limite_atteinte')
        with self.assertRaises(Stop):
            met.tester_cle(self.d, 'cabinet', 'trop court')

    def test_F05_quatre_jours_dans_le_fuseau_du_compte(self):
        self.regler()
        maintenant = self.matin()
        self.faux.debut = maintenant
        out = met.previsions(self.d, AVOCATE, maintenant=maintenant)
        self.assertEqual(out['statut'], 'ok', out)
        self.assertEqual([j['date'] for j in out['jours']], [(maintenant.date() + timedelta(days=i)).isoformat() for i in range(4)])
        self.assertEqual([j['icone'] for j in out['jours']], ['soleil', 'pluie', 'nuages', 'neige'])
        for i, j in enumerate(out['jours']):
            self.assertLessEqual(j['min'], j['max'])
            self.assertEqual(j['max'], 5 + i + 5)                                             # max des créneaux du jour (arrondi)
        self.assertEqual((out['source'], out['attribution_lien']), ('openweathermap', 'https://openweathermap.org/'))
        self.assertIn('OpenWeather', out['attribution'])
        self.assertEqual(out['ville'], 'Valbrune')

    def test_F05_jour_meme_sans_creneau_restant_aucune_valeur_inventee(self):
        self.regler()
        soir = self.matin(heure=23).replace(minute=30)
        self.faux.debut = soir
        out = met.previsions(self.d, AVOCATE, maintenant=soir)
        self.assertEqual(out['statut'], 'ok')
        self.assertIsNone(out['jours'][0]['min'])
        self.assertEqual(out['jours'][0]['libelle'], 'Plus de prévision pour aujourd’hui')
        self.assertIsInstance(out['jours'][3]['max'], int)

    def test_F05_seule_la_position_part(self):
        assistant567.save_profile(self.d, {'prenom': 'Claire'}, AVOCATE)
        self.regler()
        met.previsions(self.d, AVOCATE, maintenant=self.matin())
        appel = self.faux.appels[-1]
        self.assertEqual((appel['method'], appel['data']), ('GET', None))
        self.assertTrue(appel['url'].startswith('https://api.openweathermap.org/data/2.5/forecast?'))
        self.assertEqual(set(self.faux.params()), {'lat', 'lon', 'units', 'appid'})
        self.assertEqual((self.faux.params()['lat'], self.faux.params()['lon'], self.faux.params()['units']), ('45.123', '4.567', 'metric'))
        for interdit in ('Claire', 'DURAND', 'DOS-', 'avocate', 'Valbrune'):
            self.assertNotIn(interdit, appel['url'])

    def test_F05_homonymes_designes_par_coordonnees(self):
        met.tester_cle(self.d, 'cabinet', CLE)
        lieux = met.chercher_lieux(self.d, 'cabinet', 'Valbrune', 'fr')
        self.assertEqual(len(lieux), 2)
        self.assertEqual({l['ville'] for l in lieux}, {'Valbrune'})
        self.assertEqual(self.faux.params()['q'], 'Valbrune,FR')
        met.choisir_lieu(self.d, 'cabinet', lieux[1])
        met.activer(self.d, 'cabinet', True)
        self.faux.appels.clear()
        out = met.previsions(self.d, AVOCATE, 'Valbrune', maintenant=self.matin())
        self.assertEqual(out['statut'], 'ok')
        self.assertEqual((self.faux.params()['lat'], self.faux.params()['lon']), ('47.891', '-1.234'))
        n = len(self.faux.appels)
        autre = met.previsions(self.d, AVOCATE, 'Brumeval')                                       # une autre ville n'est jamais envoyée
        self.assertEqual((autre['statut'], autre['erreur'], len(self.faux.appels)), ('indisponible', 'ville_non_configuree', n))
        for mauvais in ('Valbrune<', 'a,b', ''):
            with self.subTest(mauvais=mauvais), self.assertRaises(Stop):
                met.chercher_lieux(self.d, 'cabinet', mauvais)

    def test_F05_changement_de_ville_cache_distinct(self):
        self.regler()
        maintenant = self.matin()
        met.previsions(self.d, AVOCATE, maintenant=maintenant)
        met.choisir_lieu(self.d, 'cabinet', LIEU_B)
        met.previsions(self.d, AVOCATE, maintenant=maintenant)
        self.assertEqual([(self.faux.params(i)['lat']) for i in range(2)], ['45.123', '47.891'])

    def test_F05_cache_trente_minutes_puis_perime_puis_indisponible(self):
        self.regler()
        maintenant = self.matin()
        horloge = [1000.0]
        lire = lambda: met.previsions(self.d, AVOCATE, maintenant=maintenant, horloge=lambda: horloge[0])
        self.assertEqual(lire()['statut'], 'ok')
        horloge[0] += 29 * 60
        self.assertEqual((lire()['statut'], len(self.faux.appels)), ('ok', 1))               # servi par le cache
        horloge[0] += 2 * 60
        self.faux.panne = True
        perime = lire()
        self.assertEqual((perime['statut'], perime['erreur'], len(perime['jours']), len(self.faux.appels)), ('perime', 'service_injoignable', 4, 2))
        horloge[0] += 7 * 3600
        self.assertEqual((lire()['statut'], lire()['jours']), ('indisponible', []))
        self.faux.panne = False
        self.assertEqual(lire()['statut'], 'ok')

    def test_F05_une_seule_requete_en_vol(self):
        self.regler()
        self.faux.attente = threading.Event()
        maintenant = self.matin()
        resultats = []
        fils = [threading.Thread(target=lambda: resultats.append(met.previsions(self.d, AVOCATE, maintenant=maintenant)['statut'])) for _ in range(2)]
        # deux demandes simultanées : la base SQLite de test n'est pas partagée entre fils, on lit donc la configuration avant
        cfg = met.configuration(self.d)
        with patch.object(met, 'configuration', lambda desk: cfg), patch.object(met, '_cle_enregistree', lambda desk: CLE), patch.object(met, '_politique', lambda desk, owner: None), \
                patch.object(met, 'fuseau', lambda desk, owner: 'Europe/Paris'):
            for f in fils:
                f.start()
            time.sleep(0.3)
            self.faux.attente.set()
            for f in fils:
                f.join(10)
        self.assertEqual((resultats, len(self.faux.appels)), (['ok', 'ok'], 1))

    def test_F05_local_strict_refuse_aucun_appel_meme_en_cache(self):
        self.regler()
        maintenant = self.matin()
        self.assertEqual(met.previsions(self.d, AVOCATE, maintenant=maintenant)['statut'], 'ok')
        dp.enregistrer(self.d, {'mode': 'local_strict', 'perimetre': 'Serveur du cabinet', 'version': self.d.settings(dp.KEY, {}).get('revision', 0)}, 'cabinet')
        met.vider_cache()
        self.faux.appels.clear()
        refus = met.previsions(self.d, AVOCATE, maintenant=maintenant)
        self.assertEqual((refus['statut'], refus['jours'], self.faux.appels), ('refuse', [], []))
        with self.assertRaises(Stop):
            met.tester_cle(self.d, 'cabinet')
        self.assertEqual(self.faux.appels, [])
        # mutation en mémoire : sans la garde de politique, l'appel part
        with patch.object(met, '_politique', lambda desk, owner: None):
            met.previsions(self.d, AVOCATE, maintenant=maintenant)
        self.assertEqual(len(self.faux.appels), 1)

    def test_F05_politique_controlee_avant_de_servir_le_cache(self):
        self.regler()
        maintenant = self.matin()
        met.previsions(self.d, AVOCATE, maintenant=maintenant)
        dp.enregistrer(self.d, {'mode': 'local_strict', 'perimetre': 'Serveur du cabinet', 'version': self.d.settings(dp.KEY, {}).get('revision', 0)}, 'cabinet')
        self.assertEqual(met.previsions(self.d, AVOCATE, maintenant=maintenant)['statut'], 'refuse')   # le relevé en cache n'est pas servi

    def test_F05_cle_jamais_journalisee_ni_renvoyee(self):
        traces = []

        class Capture(logging.Handler):
            def emit(self, record):
                traces.append(self.format(record))
        h = Capture(logging.DEBUG)
        logging.getLogger().addHandler(h)
        self.addCleanup(logging.getLogger().removeHandler, h)
        out = self.appel('accueil/reglage/tester', 'POST', {'cle': CLE, 'revision': 0}, role='administrateur', owner='cabinet')
        self.assertEqual(out['status'], '200 OK', out['body'])
        self.faux.refus = 401
        refus = self.appel('accueil/reglage/tester', 'POST', {'cle': AUTRE_CLE, 'revision': 1}, role='administrateur', owner='cabinet')
        self.assertEqual(json.loads(refus['body'])['error'], 'cle_refusee')
        self.faux.refus = None
        met.choisir_lieu(self.d, 'cabinet', LIEU_A)
        met.activer(self.d, 'cabinet', True)
        carte = self.appel('accueil/meteo')
        audit = json.dumps([list(r) for r in self.d.db.execute('SELECT * FROM audit')], ensure_ascii=False)
        reglages = json.dumps([list(r) for r in self.d.db.execute('SELECT * FROM settings')], ensure_ascii=False)
        page = self.page() + acc.reglages_html(self.d, '/p', 'administrateur')
        for lieu, texte in (('réponses', out['body'] + refus['body'] + carte['body']), ('journaux', '\n'.join(traces)), ('audit', audit), ('réglages', reglages), ('page', page)):
            for cle in (CLE, AUTRE_CLE):
                self.assertNotIn(cle, texte, lieu)
        self.assertIn('autocomplete="new-password"', page)
        self.assertNotIn('value="' + CLE, page)

    def test_F05_reponse_invalide_ou_incomplete(self):
        self.regler()
        maintenant = self.matin()
        with patch.object(self.faux, 'request', lambda *a, **k: json.dumps({'list': creneaux(maintenant, 'Europe/Paris', jours=2)['list']}).encode()):
            out = met.previsions(self.d, AVOCATE, maintenant=maintenant)
        self.assertEqual((out['statut'], out['erreur']), ('indisponible', 'reponse_meteo_invalide'))   # J+3 absent : jamais une journée inventée
        with patch.object(self.faux, 'request', lambda *a, **k: b'<html>'):
            self.assertEqual(met.previsions(self.d, AVOCATE, maintenant=maintenant)['erreur'], 'reponse_meteo_invalide')


# ====================================================================================== F04 : carte et routes
class Carte(Base):
    def test_F04_route_legere_etag_304_sans_ia(self):
        self.regler()
        premier = self.appel('accueil/meteo')
        self.assertEqual(premier['status'], '200 OK')
        etag = dict(premier['headers'])['ETag']
        self.assertEqual(json.loads(premier['body'])['statut'], 'ok')
        encore = self.appel('accueil/meteo', etag=etag)
        self.assertEqual((encore['status'], encore['body']), ('304 Not Modified', ''))
        self.assertEqual(len(self.faux.appels), 1)
        self.assertIn('private', dict(premier['headers'])['Cache-Control'])

    def test_F04_carte_masquee_par_la_personne_rien_charge(self):
        self.regler()
        self.assertIn('data-f96-meteo', self.page(AVOCATE, 'avocat'))
        self.assertEqual(self.appel('accueil/options', 'POST', {'meteo_visible': False})['status'], '200 OK')
        self.assertTrue(assistant567.profile(self.d, AVOCATE)['meteo_masquee'])
        self.assertNotIn('data-f96-meteo', self.page(AVOCATE, 'avocat'))
        out = self.appel('accueil/meteo')
        self.assertEqual((json.loads(out['body'])['statut'], self.faux.appels), ('masque', []))
        self.assertIn('data-f96-meteo', self.page())                                        # les autres comptes gardent la carte

    def test_F04_carte_chargee_apres_affichage_avec_attribution(self):
        self.regler()
        html = self.page(AVOCATE, 'avocat')
        carte = re.search(r'<section class="f96-meteo".*?</section>', html, re.S).group(0)
        self.assertIn('data-url="/p/api440/m5689/accueil/meteo"', carte)
        self.assertIn('href="https://openweathermap.org/"', carte)
        self.assertIn('Données météo fournies par OpenWeather', carte)
        self.assertEqual(self.faux.appels, [])                                                # le rendu de la page ne consulte pas le service
        self.assertNotIn('Changer la ville', carte)                                            # réglage : administrateur seulement
        self.assertIn('Changer la ville', self.page())
        js = (STATIC / 'v5696-accueil.js').read_text(encoding='utf-8')
        self.assertNotIn('innerHTML = d.jours', js)

    def test_F03_reglage_reserve_a_l_administrateur_et_version(self):
        from agent.standalone_auth import allowed
        self.assertFalse(allowed('avocat', 'POST', '/api440/m5689/accueil/reglage/tester'))
        self.assertTrue(allowed('avocat', 'POST', '/api440/m5689/accueil/options'))
        refus = self.appel('accueil/reglage/tester', 'POST', {'cle': CLE})
        self.assertEqual(refus['status'], '403 Forbidden')
        self.assertFalse(met.chemin_cle(self.d).exists())
        ok = self.appel('accueil/reglage/tester', 'POST', {'cle': CLE, 'revision': 0}, role='administrateur', owner='cabinet')
        self.assertEqual(ok['status'], '200 OK')
        perime = self.appel('accueil/reglage/lieu', 'POST', {'lieu': LIEU_A, 'revision': 0}, role='administrateur', owner='cabinet')
        self.assertEqual(perime['status'], '409 Conflict')
        self.assertFalse(met.configuration(self.d)['valide'])                                   # page périmée : rien d'écrit
        lieux = json.loads(self.appel('accueil/reglage/lieux', 'POST', {'ville': 'Valbrune'}, role='administrateur', owner='cabinet')['body'])['lieux']
        self.assertEqual(len(lieux), 2)
        rev = met.configuration(self.d)['revision']
        self.assertEqual(self.appel('accueil/reglage/lieu', 'POST', {'lieu': lieux[0], 'revision': rev}, role='administrateur', owner='cabinet')['status'], '200 OK')
        actif = self.appel('accueil/reglage/activer', 'POST', {'actif': True, 'revision': rev + 1}, role='administrateur', owner='cabinet')
        self.assertEqual(actif['status'], '200 OK', actif['body'])
        self.assertTrue(met.configuration(self.d)['enabled'])

    def test_F03_carte_de_reglage_dans_connexions(self):
        html = acc.reglages_html(self.d, '/p', 'administrateur')
        self.assertIn('href="https://home.openweathermap.org/users/sign_in"', html)
        self.assertIn('type="password"', html)
        self.assertIn('disabled', re.search(r'<input type="checkbox" name="actif"[^>]*>', html).group(0))     # pas d'activation sans clé testée
        self.assertEqual(acc.reglages_html(self.d, '/p', 'avocat'), '')
        self.d.c['meteo5696'] = {'enabled': False}
        self.regler()
        self.assertFalse(met.configuration(self.d)['enabled'])                                 # coupure par la configuration du serveur
        self.assertEqual(met.previsions(self.d, AVOCATE)['statut'], 'desactive')

    def test_F04_textes_dans_les_quatre_langues(self):
        from agent import i18n5631
        self.assertEqual([p for p in i18n5631.check() if p[1].startswith('f96.')], [])
        for lang in ('fr', 'en', 'de', 'es'):
            for icone in met.ICONES + ('fin_de_journee',):
                self.assertIn('f96.meteo.' + icone, i18n5631.catalog(lang))


# ====================================================================================== I01 : ordre d'Aujourd'hui
class Ordre(Base):
    def echeance(self, ident, statut, jours=0, matter='DOS-001'):
        due = (datetime.now(ZoneInfo('Europe/Paris')).date() + timedelta(days=jours)).isoformat()
        MESURE.echeance(self.d, ident, matter, due, statut)
        self.d.db.commit()

    def test_I01_ordre_des_zones(self):
        self.semer()
        html = self.page()
        positions = [html.index(x) for x in ('class="f96-bandeau"', 'id="ax95-actuellement"', 'class="f96-rang"', 'id="f96-decider"', 'class="f96-journee"', 'id="a89-travail"')]
        self.assertEqual(positions, sorted(positions))
        self.assertEqual(html.count('id="c530-day"'), 1)
        self.assertEqual(html.count('FILE-COMPLETE'), 1)                                       # la file complète reste, une seule fois, dans « Voir la suite »
        self.assertIn('<details class="f96-suite" data-f96-suite>', html)
        css = (STATIC / 'v5696-accueil.css').read_text(encoding='utf-8')
        self.assertIn('grid-template-columns: minmax(0, 2fr) minmax(0, 1fr)', css)
        self.assertIn('grid-template-rows: repeat(4, auto)', css)

    def test_I01_trois_premieres_actions_puis_la_suite(self):
        for i in range(5):
            self.echeance('ech-%d' % i, 'a_confirmer', jours=i + 1)
        html = self.page()
        zone = re.search(r'<div id="f96-lignes-zone">(.*?)</div><p class="f96-annonce"', html, re.S).group(1)
        self.assertEqual(zone.count('<li class="f96-ligne"'), 3)
        self.assertEqual(zone.count('>Vérifier la date</a>'), 3)
        self.assertIn('À vérifier', zone)
        self.assertIn('data-f96-action="echeance"', zone)
        self.assertIn('href="/p/echeances#e-ech-0"', zone)                                       # sans script : le lien habituel

    def test_I01_ma_journee_seulement_les_echeances_confirmees(self):
        self.echeance('ech-ok', 'confirmee')
        self.echeance('ech-candidat', 'a_confirmer')
        self.echeance('ech-demain', 'confirmee', jours=1)
        journee = acc.echeances_du_jour_html(self.d, '/p', 'cabinet', 'administrateur')
        self.assertIn('e-ech-ok', journee)
        self.assertNotIn('ech-candidat', journee)                                               # une date tirée d'une pièce n'est jamais « confirmée »
        self.assertNotIn('ech-demain', journee)
        with patch('agent.membres5650.hidden_for', return_value=frozenset({'DOS-001'})):
            self.assertIn('Aucune échéance confirmée', acc.echeances_du_jour_html(self.d, '/p', AVOCATE, 'avocat'))

    def test_I01_source_en_panne_jamais_un_zero(self):
        with patch('agent.file5651.items', side_effect=RuntimeError('fictif')):
            lignes, total, complet = acc.lignes_html(self.d, '/p', 'cabinet', 'administrateur')
        self.assertFalse(complet)
        self.assertIn('Une source n’a pas pu être lue', lignes)
        self.assertNotIn('Rien à décider', lignes)
        with patch('agent.echeances450.ensure_schema', side_effect=RuntimeError('fictif')):
            self.assertIn('source indisponible', acc.echeances_du_jour_html(self.d, '/p', 'cabinet', 'administrateur'))

    def test_I01_ordre_personnel_conserve(self):
        self.semer()
        tableau5688.enregistrer(self.d, 'cabinet', {'ordre': ['file', 'actuellement'], 'accent': ''})
        html = self.page()
        self.assertNotIn('class="f96-rang"', html)
        self.assertGreater(html.index('id="ax95-actuellement"'), html.index('FILE-COMPLETE'))
        self.assertIn('class="f96-bandeau"', html)

    def test_I01_bloc_masque_absent_du_rang(self):
        tableau5688.enregistrer(self.d, 'cabinet', {'ordre': list(tableau5688.IDS), 'masques': ['journee'], 'accent': ''})
        html = self.page()
        self.assertIn('f96-rang f96-rang-seul', html)
        self.assertNotIn('class="f96-journee"', html)

    def test_I01_aucun_script_en_ligne_ni_style(self):
        self.regler()
        html = acc.bandeau_html(self.d, 'cabinet') + acc.options_html(self.d, 'cabinet') + acc.carte_html(self.d, 'cabinet', 'administrateur', '/p') + \
            acc.reglages_html(self.d, '/p', 'administrateur') + acc.decider_html(self.d, '/p', 'cabinet', 'administrateur', '<div>x</div>')
        for attrs, contenu in re.findall(r'<script([^>]*)>(.*?)</script>', html, re.S):
            self.assertTrue(('defer' in attrs and 'src=' in attrs and not contenu.strip()) or 'type="application/json"' in attrs, attrs)
        self.assertNotIn('style=', html)
        self.assertNotRegex(html, r'\son[a-z]+=')


# ====================================================================================== I02 : fenêtre flottante
class Fenetre(Base):
    def env(self, owner='cabinet', role='administrateur'):
        return {'HTTP_X_AXIORHUB_USER': owner, 'HTTP_X_AXIORHUB_ROLE': role, 'REQUEST_METHOD': 'GET'}

    def compter(self):
        return {t: self.d.db.execute('SELECT COUNT(*) FROM %s' % t).fetchone()[0] for t in ('prepared_items5689', 'deadlines450', 'audit', 'manual_drafts')
                if self.d.db.execute("SELECT 1 FROM sqlite_master WHERE name=?", (t,)).fetchone()}

    def test_I02_table_fermee_et_inconnu_404(self):
        for nature, cle in (('autre', 'doc-x'), ('document', 'doc-inexistant'), ('echeance', 'ech-inexistante'), ('reponse', 'a' * 64), ('document', '../etc')):
            with self.subTest(nature=nature, cle=cle):
                out = self.appel('accueil/action', args={'nature': nature, 'cle': cle})
                self.assertEqual(out['status'], '404 Not Found', out['body'])
                self.assertNotIn('html', json.loads(out['body']))

    def test_I02_document_meme_service_et_memes_formulaires_que_la_page(self):
        self.d.c['nextcloud'].update({'url': 'https://cloud.example.test', 'local_path': self.t.name})   # stockage local fictif (fichier absent : aperçu indisponible, dit)
        MESURE.prepare(self.d, 'liv1', 'Projet de contrat DURAND', 'contrat', 'DOS-001')
        self.d.db.commit()
        avant = self.compter()
        out = self.appel('accueil/action', args={'nature': 'document', 'cle': 'doc-liv1'}, role='administrateur', owner='cabinet')
        self.assertEqual(out['status'], '200 OK', out['body'])
        data = json.loads(out['body'])
        from agent import livrables_web5689 as lw
        _titre, corps = lw._detail_page(self.d, self.env(), {'csrf': 'jeton'}, '/p', 'doc-liv1', {'return_to': '/p/aujourdhui'}, acc._capture)
        sans_cle = lambda h: re.sub(r'name="cle" value="[0-9a-f]{32}"', '', h)                  # clé d'idempotence tirée à chaque rendu
        self.assertEqual(sans_cle(data['html']), sans_cle(corps))
        self.assertIn('m5689/livrables/doc-liv1/relecture', data['html'])
        self.assertEqual((data['titre'], data['page']), ('Projet de contrat DURAND', '/p/livrables/doc-liv1'))
        self.assertEqual(self.compter(), avant)                                                 # GET ne crée, ne valide, n'envoie rien

    def test_I02_dossier_reserve_403_sans_contenu(self):
        MESURE.prepare(self.d, 'liv2', 'Projet réservé ZORVAL', 'contrat', 'DOS-004')
        MESURE.echeance(self.d, 'ech-r', 'DOS-004', datetime.now().date().isoformat(), 'a_confirmer')
        self.d.db.commit()
        with patch('agent.membres5650.hidden_for', return_value=frozenset({'DOS-004'})):
            for nature, cle, statuts in (('document', 'doc-liv2', ('403 Forbidden', '404 Not Found')), ('echeance', 'ech-r', ('403 Forbidden',))):
                out = self.appel('accueil/action', args={'nature': nature, 'cle': cle})
                self.assertIn(out['status'], statuts, (nature, out['body']))     # document : même réponse que sa page (introuvable pour ce compte)
                self.assertNotIn('ZORVAL', out['body'])
        with patch('agent.membres5650.hidden_for', return_value=frozenset()):                   # contre-épreuve
            self.assertEqual(self.appel('accueil/action', args={'nature': 'echeance', 'cle': 'ech-r'})['status'], '200 OK')

    def test_I02_reponse_meme_formulaire_que_la_page_du_courriel(self):
        cle = 'b' * 64
        self.d.db.execute('CREATE TABLE IF NOT EXISTS manual_drafts(mail_key TEXT PRIMARY KEY, data TEXT, updated TEXT)')
        valeur = {'body': 'Cher Confrère,\nVoici notre réponse fictive.', 'requires_decision': True, 'limits': []}
        self.d.db.execute('INSERT INTO manual_drafts(mail_key,data,updated) VALUES(?,?,?)', (cle, json.dumps({'matter': 'DOS-002', 'result': valeur}), '2026-01-01'))
        self.d.db.commit()
        out = self.appel('accueil/action', args={'nature': 'reponse', 'cle': cle})
        self.assertEqual(out['status'], '200 OK', out['body'])
        from agent import reponses5671
        html = json.loads(out['body'])['html']
        from agent.relecture56100 import revision_proposition   # 5.6.100 (lot B, R09) : le formulaire porte la version affichée
        self.assertIn(reponses5671.editor_html(cle, valeur, None, revision=revision_proposition({'matter': 'DOS-002', 'result': valeur})), html)
        self.assertIn('data-c5671="m5671/reponse/deposer"', html)
        self.assertEqual(json.loads(out['body'])['page'], '/p/mail?key=' + cle)
        with patch('agent.membres5650.hidden_for', return_value=frozenset({'DOS-002'})):
            self.assertEqual(self.appel('accueil/action', args={'nature': 'reponse', 'cle': cle})['status'], '404 Not Found')   # 5.6.100 (R04) : même 404 qu'absent

    def test_I02_echeance_a_verifier_jamais_presentee_comme_confirmee(self):
        MESURE.echeance(self.d, 'ech-v', 'DOS-001', datetime.now().date().isoformat(), 'a_confirmer')
        self.d.db.commit()
        avant = self.compter()
        data = json.loads(self.appel('accueil/action', args={'nature': 'echeance', 'cle': 'ech-v'})['body'])
        self.assertIn('data-verifiee="0"', data['html'])
        self.assertIn('à vérifier', data['html'])
        self.assertIn('href="/p/echeances#e-ech-v"', data['html'])
        self.assertNotIn('<form', data['html'])                                                 # la décision reste dans Échéances, seul circuit de validation
        self.assertEqual(self.compter(), avant)

    def test_I02_liens_du_travail_prepare_et_controleur(self):
        MESURE.prepare(self.d, 'liv3', 'Projet de courrier MARTIN', 'courrier', 'DOS-002')
        MESURE.prepare(self.d, 'liv4', 'Réponse au confrère', 'courriel', 'DOS-002', depot='depose')
        self.d.db.commit()
        html = self.page()
        self.assertRegex(html, r'href="/p/livrables/doc-liv3\?return_to=[^"]*"[^>]*data-f96-action="document" data-f96-cle="doc-liv3"')
        js = (STATIC / 'v5696-accueil.js').read_text(encoding='utf-8')
        for attendu in ('showModal', 'AbortController', "addEventListener('cancel'", 'demande.abort()', 'ev.button !== 0', 'focus({ preventScroll: true })', 'HTMLDialogElement'):
            self.assertIn(attendu, js)
        self.assertNotIn('<iframe', js)

    def test_I02_rang_actualise_par_route(self):
        MESURE.echeance(self.d, 'ech-a', 'DOS-001', datetime.now().date().isoformat(), 'a_confirmer')
        self.d.db.commit()
        out = json.loads(self.appel('accueil/rang')['body'])
        self.assertEqual((out['total'], out['complet']), (1, True))
        self.assertIn('data-f96-ligne="echeance:ech-a"', out['html'])


if __name__ == '__main__':
    unittest.main()
