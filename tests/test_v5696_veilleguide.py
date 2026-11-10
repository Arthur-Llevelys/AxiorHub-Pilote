"""5.6.96, lot veille-guide (W) : I05 (veille juridique et économique tous les trois jours, planificateur unique) et I06 (guide
d'installation : reprise, « Ajouter une fonction » sans ressaisir les secrets, cartes des autres lots, récapitulatif précis).

Données fictives, dates relatives au jour, aucun service réel : flux, Tavily et connecteur sont des faux qui enregistrent leurs appels."""
from datetime import date, datetime, timedelta, timezone
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import sys
import threading
import types
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

import test_agent as fixtures
import test_v5631 as t31
from agent import veille5632 as veille
from agent.common import Stop, load_config, private_json
from agent.desk import Desk

PARIS = ZoneInfo('Europe/Paris')


def aujourd_hui():
    return datetime.now(PARIS).date()


def instant(jour, heure, minute=0, tz=PARIS):
    return datetime(jour.year, jour.month, jour.day, heure, minute, tzinfo=tz)


def stamp(jour, heure, minute=0):
    return instant(jour, heure, minute).timestamp()


def changements_d_heure():
    """Prochains passages à l'heure d'été et à l'heure d'hiver à Paris, cherchés à partir d'aujourd'hui (aucune date fixe)."""
    printemps = automne = None
    jour = aujourd_hui()
    for i in range(1, 400):
        d = jour + timedelta(days=i)
        avant, apres = instant(d, 0).utcoffset(), instant(d, 12).utcoffset()
        if apres > avant and printemps is None:
            printemps = d
        if apres < avant and automne is None:
            automne = d
    return printemps, automne


def rss(*titres):
    """Flux RSS fictif, éléments datés d'aujourd'hui."""
    quand = datetime.now(timezone.utc).strftime('%a, %d %b %Y %H:%M:%S +0000')
    items = ''.join('<item><title>%s</title><link>https://www.example.test/%d</link><pubDate>%s</pubDate><description>%s</description></item>'
                    % (t, i, quand, t) for i, t in enumerate(titres))
    return ('<?xml version="1.0"?><rss version="2.0"><channel><title>Flux fictif</title>%s</channel></rss>' % items).encode()


class FauxTavily:
    """Faux client HTTP : enregistre chaque requête (corps compris) et rend un résultat daté du jour."""

    def __init__(self):
        self.requetes = []

    def request(self, method, url, body=None, headers=None, limit=0):
        self.requetes.append(json.loads(body.decode()))
        return json.dumps({'results': [{'url': 'https://www.example.test/actualite', 'title': 'Actualité fictive', 'content': 'bail commercial',
                                        'published_date': datetime.now(timezone.utc).isoformat()}]}).encode()


class Base(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.EngineTests('test_observation_has_no_mail_write')
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.desk = Desk(self.f.c)
        self.addCleanup(self.desk.db.close)
        veille.schema(self.desk)

    def routine(self, ancre, **extra):
        data = {'enabled': True, 'themes': 'Baux commerciaux : bail commercial, loyer', 'mode': 'interval', 'interval_days': 3,
                'hour': '07:30', 'tz': 'Europe/Paris', 'anchor_date': ancre.isoformat(), 'feeds': ['cnil'], 'custom_feeds': [], 'connector': False}
        data.update(extra)
        return veille.save_profile(self.desk, 'cabinet', data)

    def travaux(self):
        return self.desk.db.execute("SELECT COUNT(*) FROM jobs WHERE kind='veille5632_collect'").fetchone()[0]

    def passages(self):
        return [r['slot'] for r in self.desk.db.execute('SELECT slot FROM veille_runs5632 ORDER BY id')]


# ------------------------------------------------------------------------------------------------------------- I05
class VeilleTroisJours(Base):
    def test_I05_trois_jours_calendaires_comptes_en_dates_locales_depuis_l_ancre(self):
        ancre = aujourd_hui() + timedelta(days=5)
        p = self.routine(ancre)
        self.assertEqual((p['mode'], p['interval_days'], p['anchor_date']), ('interval', 3, ancre.isoformat()))
        self.assertEqual([veille.jour_intervalle(p, ancre + timedelta(days=k)) for k in range(-3, 7)],
                         [False, False, False, True, False, False, True, False, False, True])          # jamais avant l'ancre
        self.assertIsNone(veille.current_slot(p, instant(ancre - timedelta(days=1), 23)))           # rien avant l'ancre
        self.assertIsNone(veille.current_slot(p, instant(ancre, 7, 29)))
        premier = veille.current_slot(p, instant(ancre, 8))
        self.assertEqual(premier, 'slot-%sT07:30-Europe/Paris' % ancre.isoformat())
        self.assertEqual(veille.current_slot(p, instant(ancre + timedelta(days=2), 22)), premier)   # un seul créneau entre deux passages
        self.assertEqual(veille.current_slot(p, instant(ancre + timedelta(days=3), 7, 31)),
                         'slot-%sT07:30-Europe/Paris' % (ancre + timedelta(days=3)).isoformat())
        self.assertEqual(veille.next_due(p, instant(ancre, 8)), instant(ancre + timedelta(days=3), 7, 30))

    def test_I05_changement_d_heure_dates_locales_pas_72_heures_utc(self):
        printemps, automne = changements_d_heure()
        for bascule in (printemps, automne):
            p = {'enabled': True, 'mode': 'interval', 'interval_days': 3, 'anchor_date': (bascule - timedelta(days=1)).isoformat(),
                 'hour': '07:30', 'tz': 'Europe/Paris', 'themes': [{'name': 'x', 'keywords': ['x'], 'domaine': 'juridique'}]}
            premier = veille.next_due(p, instant(bascule - timedelta(days=1), 6))
            second = veille.next_due(p, premier + timedelta(minutes=1))
            self.assertEqual((premier.hour, premier.minute, second.hour, second.minute), (7, 30, 7, 30))   # même heure locale
            self.assertEqual(second.date() - premier.date(), timedelta(days=3))
            self.assertNotEqual(second.astimezone(timezone.utc) - premier.astimezone(timezone.utc), timedelta(hours=72))

    def test_I05_heure_inexistante_decalee_et_heure_repetee_un_seul_passage(self):
        printemps, automne = changements_d_heure()
        p = {'enabled': True, 'mode': 'interval', 'interval_days': 3, 'anchor_date': printemps.isoformat(), 'hour': '02:30',
             'tz': 'Europe/Paris', 'themes': [{'name': 'x', 'keywords': ['x'], 'domaine': 'juridique'}]}
        due = veille.next_due(p, instant(printemps, 0))
        self.assertEqual((due.date(), due.hour, due.minute), (printemps, 3, 0))                    # première minute valide
        # Heure répétée : 02:30 existe deux fois ; un seul créneau, une seule collecte
        self.routine(aujourd_hui() + timedelta(days=1), hour='02:30')
        prof = veille.profile(self.desk)
        prof['anchor_date'] = automne.isoformat()
        self.desk.setting('veille5632:profile:cabinet', prof)
        premiere = datetime(automne.year, automne.month, automne.day, 2, 40, tzinfo=PARIS)           # fold=0 : première occurrence
        seconde = datetime(automne.year, automne.month, automne.day, 2, 40, fold=1, tzinfo=PARIS)
        self.assertEqual(seconde.astimezone(timezone.utc) - premiere.astimezone(timezone.utc), timedelta(hours=1))
        self.assertEqual(veille.current_slot(prof, premiere), veille.current_slot(prof, seconde))
        veille.schedule(self.desk, premiere.timestamp())
        veille.schedule(self.desk, seconde.timestamp())
        self.assertEqual((self.travaux(), len(self.passages())), (1, 1))

    def test_I05_un_seul_passage_par_creneau_et_reprise_apres_arret_sans_rafale(self):
        ancre = aujourd_hui() + timedelta(days=5)
        self.routine(ancre)
        self.assertEqual(veille.schedule(self.desk, stamp(ancre, 7)), [])                           # avant l'heure : rien
        self.assertEqual(len(veille.schedule(self.desk, stamp(ancre, 8))), 1)
        self.assertEqual(veille.schedule(self.desk, stamp(ancre, 9)), [])                           # même créneau, travail vivant
        self.desk.db.execute("UPDATE veille_runs5632 SET state='done'")
        self.desk.db.execute("UPDATE jobs SET status='done' WHERE kind='veille5632_collect'")
        self.desk.db.commit()
        self.assertEqual(veille.schedule(self.desk, stamp(ancre, 10)), [])                          # passage confirmé : jamais relancé
        # Arrêt du service pendant dix jours : seul le dernier créneau échu est traité (pas de rafale)
        self.assertEqual(len(veille.schedule(self.desk, stamp(ancre + timedelta(days=10), 8))), 1)
        self.assertEqual(self.passages(), ['slot-%sT07:30-Europe/Paris' % ancre.isoformat(),
                                           'slot-%sT07:30-Europe/Paris' % (ancre + timedelta(days=9)).isoformat()])
        self.assertEqual(self.travaux(), 2)

    def test_I05_deux_cycles_concurrents_un_seul_travail(self):
        ancre = aujourd_hui() + timedelta(days=5)
        self.routine(ancre)
        barriere, erreurs = threading.Barrier(2), []

        def cycle():
            desk = Desk(self.f.c)
            try:
                barriere.wait(10)
                veille.schedule(desk, stamp(ancre, 8))
            except Exception as ex:   # noqa: BLE001
                erreurs.append(ex)
            finally:
                desk.db.close()
        fils = [threading.Thread(target=cycle) for _ in range(2)]
        for t in fils:
            t.start()
        for t in fils:
            t.join(30)
        self.assertEqual(erreurs, [])
        self.assertEqual((self.travaux(), len(self.passages())), (1, 1))
        run = self.desk.db.execute('SELECT job_id FROM veille_runs5632').fetchone()
        self.assertTrue(run['job_id'])                                                              # passage et travail liés

    def test_I05_activation_sans_theme_ni_source_attend(self):
        ancre = aujourd_hui() + timedelta(days=5)
        p = self.routine(ancre, themes='')
        self.assertEqual(veille.attente(self.desk, p), 'selection')
        self.assertEqual(veille.schedule(self.desk, stamp(ancre, 8)), [])
        self.assertEqual(veille.state(self.desk)['next_run'], '')                                   # aucun passage annoncé
        p = veille.save_profile(self.desk, 'cabinet', {'themes': 'Baux : bail', 'feeds': [], 'custom_feeds': '', 'connector': False, 'tavily': False})
        self.assertEqual(veille.attente(self.desk, p), 'source')
        self.assertEqual(veille.schedule(self.desk, stamp(ancre, 8)), [])
        # Thèmes économiques seuls : le connecteur juridique n'est pas une source utilisable pour eux
        p = veille.save_profile(self.desk, 'cabinet', {'themes': '', 'themes_eco': 'Conjoncture : défaillances', 'connector': True})
        self.assertEqual(veille.attente(self.desk, p), 'source')
        p = veille.save_profile(self.desk, 'cabinet', {'feeds': ['cnil']})
        self.assertEqual(veille.attente(self.desk, p), '')
        self.assertEqual(p['anchor_date'], ancre.isoformat())                                       # la sélection ne décale pas l'ancre
        self.assertEqual(len(veille.schedule(self.desk, stamp(ancre, 8))), 1)
        self.assertEqual(self.travaux(), 1)

    def test_I05_desactivation_effective_meme_apres_mise_en_file(self):
        ancre = aujourd_hui() + timedelta(days=5)
        self.routine(ancre)
        veille.schedule(self.desk, stamp(ancre, 8))
        slot = self.passages()[0]
        veille.save_profile(self.desk, 'cabinet', {'enabled': False})
        appels = []
        out = veille.collect(self.desk, {'owner': 'cabinet', 'slot': slot}, fetch=lambda url: appels.append(url) or rss('bail commercial'))
        self.assertTrue(out['skipped'])
        self.assertEqual(appels, [])                                                                # aucune source interrogée
        self.assertEqual(self.desk.db.execute('SELECT state FROM veille_runs5632').fetchone()[0], 'cancelled')
        self.assertIsNone(veille.current_slot(veille.profile(self.desk), instant(ancre + timedelta(days=3), 8)))

    def test_I05_cadence_modifiee_annule_l_ancien_creneau_et_recalcule_une_ancre_future(self):
        ancre = aujourd_hui() + timedelta(days=1)
        self.routine(ancre)
        prof = veille.profile(self.desk)
        prof['anchor_date'] = (aujourd_hui() - timedelta(days=6)).isoformat()                       # routine en service depuis six jours
        self.desk.setting('veille5632:profile:cabinet', prof)
        veille.schema(self.desk)
        self.desk.db.execute("INSERT INTO veille_runs5632(owner,slot,state,created) VALUES('cabinet','slot-ancien-passage','done','x')")
        self.desk.db.execute("INSERT INTO veille_runs5632(owner,slot,state,created) VALUES('cabinet',?,'pending','x')",
                             ('slot-%sT07:30-Europe/Paris' % prof['anchor_date'],))
        self.desk.db.commit()
        p = veille.save_profile(self.desk, 'cabinet', {'hour': '18:15'})
        self.assertGreaterEqual(date.fromisoformat(p['anchor_date']), aujourd_hui())               # ancre future, jamais rattrapée
        self.assertGreaterEqual(veille.next_due(p), datetime.now(timezone.utc))
        self.assertEqual(len(self.passages()), 2)                                                   # historique conservé
        out = veille.collect(self.desk, {'owner': 'cabinet', 'slot': 'slot-%sT07:30-Europe/Paris' % prof['anchor_date']},
                             fetch=lambda url: self.fail('collecte d’un créneau de l’ancienne cadence'))
        self.assertTrue(out['skipped'])

    def test_I05_validation_serveur_intervalle_ancre_fuseau(self):
        for data, code in (({'interval_days': 0}, 'intervalle_veille_invalide'), ({'interval_days': 31}, 'intervalle_veille_invalide'),
                           ({'interval_days': 'trois'}, 'intervalle_veille_invalide'), ({'anchor_date': 'demain'}, 'ancre_veille_invalide'),
                           ({'anchor_date': (aujourd_hui() - timedelta(days=1)).isoformat()}, 'ancre_veille_invalide'),
                           ({'tz': 'Europe/Valbrune'}, 'fuseau_inconnu'), ({'mode': 'toutes_les_heures'}, 'frequence_veille_invalide')):
            with self.assertRaisesRegex(Stop, code):
                veille.save_profile(self.desk, 'cabinet', {'mode': 'interval', **data})
        self.assertIsNone(self.desk.settings('veille5632:profile:cabinet', None))                  # rien d'enregistré

    def test_I05_frequences_et_selections_personnalisees_conservees(self):
        veille.save_profile(self.desk, 'cabinet', {'enabled': True, 'themes': 'Baux : bail', 'mode': 'weekly', 'weekday': 2, 'hour': '18:00'})
        self.assertEqual(veille.propose(self.desk)['mode'], 'weekly')                               # aucune routine imposée
        p = veille.save_profile(self.desk, 'cabinet', {'themes_eco': 'Conjoncture : défaillances, taux'})
        self.assertEqual((p['mode'], p['weekday'], p['hour']), ('weekly', 2, '18:00'))
        self.assertEqual([(t['name'], t['domaine']) for t in p['themes']], [('Baux', 'juridique'), ('Conjoncture', 'economique')])
        p = veille.save_profile(self.desk, 'cabinet', {'themes': 'Travail : licenciement'})          # appelant historique : économie conservée
        self.assertEqual([(t['name'], t['domaine']) for t in p['themes']], [('Travail', 'juridique'), ('Conjoncture', 'economique')])
        # Ancien profil sans domaine : thèmes juridiques
        self.desk.setting('veille5632:profile:autre', {'themes': [{'name': 'Ancien', 'keywords': ['ancien'], 'exclusions': []}]})
        self.assertEqual(veille.profile(self.desk, 'autre')['themes'][0]['domaine'], 'juridique')
        # Veille désactivée volontairement : la proposition ne la réactive pas
        self.desk.setting('proactive568:profile:tiers', {'legal_fields': ['Baux'], 'news_enabled': False})
        self.assertFalse(veille.propose(self.desk, 'tiers')['enabled'])
        self.assertEqual(veille.propose(self.desk, 'neuf')['mode'], 'interval')                    # routine proposée à une veille neuve
        self.assertEqual(veille.propose(self.desk, 'neuf')['interval_days'], 3)

    def test_I05_actualiser_ne_decale_pas_le_cycle(self):
        ancre = aujourd_hui() + timedelta(days=2)
        p = self.routine(ancre)
        avant = veille.next_due(p)
        out = veille.refresh(self.desk, 'cabinet')
        self.assertTrue(out['slot'].startswith('manual-'))
        apres = veille.profile(self.desk)
        self.assertEqual((apres['anchor_date'], veille.next_due(apres)), (ancre.isoformat(), avant))

    def test_I05_panne_d_une_source_veille_partielle_jamais_vide(self):
        self.routine(aujourd_hui() + timedelta(days=1), feeds=['cnil', 'justice'])

        def fetch(url):
            if 'justice' in url:
                raise Stop('http_503')
            return rss('Nouveau bail commercial : loyer plafonné')
        out = veille.collect(self.desk, {'owner': 'cabinet', 'slot': 'manual-essai'}, fetch=fetch)
        self.assertEqual((out['state'], out['added']), ('partial', 1))
        st = veille.state(self.desk)
        self.assertEqual(st['last_status'], 'partiel')
        html = veille.card_html(self.desk, '/agent-courriel')
        self.assertIn('partiel', html)
        self.assertNotIn('Rien de nouveau', html)

    def test_I05_aucun_contenu_de_dossier_dans_les_requetes_publiques(self):
        """Mot-clé désignant un dossier fictif (référence DOS-001) : requête refusée AVANT tout envoi ou décompte ; le connecteur
        juridique ne reçoit que les thèmes juridiques."""
        from agent.vault567 import write
        key = write(Path(self.desk.c['state_dir']) / 'vault' / 'essai' / 'tavily.secret', 'tvly-cle-fictive-essai')
        self.desk.setting('veille5632:tavily_secret', key)
        self.routine(aujourd_hui() + timedelta(days=1), themes='Baux : bail commercial\nSuivi : DOS-001, audience', feeds=[], connector=True, tavily=True,
                     themes_eco='Conjoncture : défaillances')
        http = FauxTavily()
        recherches = []

        class Checker:
            def __init__(self, desk, call):
                self.item = {'provider': 'faux'}

            def available(self, tool):
                return True

            def run(self, tool, args):
                recherches.append(args['query'])
                return {'results': []}
        with patch('agent.controle5632.Checker', Checker):
            out = veille.collect(self.desk, {'owner': 'cabinet', 'slot': 'manual-essai'}, http=http)
        envoye = ' '.join(r['query'] for r in http.requetes) + ' ' + ' '.join(recherches)
        self.assertNotIn('DOS-001', envoye)
        self.assertEqual(sorted(r['query'] for r in http.requetes), ['bail commercial actualité juridique', 'défaillances actualité économique'])
        self.assertEqual(recherches, ['bail commercial'])                                           # ni « Suivi » (refusé) ni l'économie
        refus = [s for s in out['sources'] if s.get('error') == 'donnee_de_dossier_dans_la_requete']
        self.assertEqual(len(refus), 2)                                                             # connecteur et Tavily
        self.assertEqual(veille.usage(self.desk)['calls'], 2)                                       # la requête refusée n'est pas décomptée


# ------------------------------------------------------------------------------------------------------------- I06
class Formulaires(HTMLParser):
    def __init__(self):
        super().__init__()
        self.pile, self.imbriques, self.formulaires, self.selection = [], 0, [], {}
        self._select = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == 'form':
            if self.pile:
                self.imbriques += 1
            self.pile.append(a)
            self.formulaires.append(a)
        elif tag == 'select':
            self._select = a.get('name')
        elif tag == 'option' and self._select and 'selected' in a:
            self.selection[self._select] = a.get('value')
        elif tag == 'input' and a.get('name') == 'interval_days':
            self.selection['interval_days'] = a.get('value')

    def handle_endtag(self, tag):
        if tag == 'form' and self.pile:
            self.pile.pop()
        elif tag == 'select':
            self._select = None


class Guide(t31.Web):
    def installation_existante(self):
        """Cabinet déjà installé : messagerie active avec un mot de passe au coffre, fonctions installées, veille hebdomadaire réglée."""
        from agent.vault567 import write
        cfg = json.loads(self.config.read_text(encoding='utf-8'))
        secret = write(Path(cfg['state_dir']) / 'vault' / 'essai' / 'imap.secret', 'mot-de-passe-fictif-imap')
        cfg['mail'].update({'password_file': secret, 'enabled': True})
        cfg['installation'] = {'done': True, 'features': ['documents', 'messagerie'], 'partial': ['documents'], 'guide_version': 1}
        private_json(self.config, cfg)
        desk = self.desk()
        veille.save_profile(desk, 'cabinet', {'enabled': True, 'themes': 'Baux : bail', 'mode': 'weekly', 'weekday': 4, 'hour': '17:45'})
        desk.setting('assistant567:profile:cabinet', {'prenom': 'Claire', 'avatar': 'portrait_a'})
        return desk, secret

    def test_I06_session_interrompue_reprend_a_la_meme_etape_avec_ses_reponses(self):
        from agent import guide5631 as g
        desk = self.desk()
        s = g.start(desk, 'cabinet', ['messagerie', 'veille'])
        r = g.save_step(desk, 'cabinet', s['id'], s['revision'], 'profil', {'prenom': 'Camille', 'nom': 'MARTIN', 'barreau': 'Valbrune'}, 'save')
        r = g.save_step(desk, 'cabinet', s['id'], r['revision'], 'messagerie', {'host': 'imap.example.test', 'username': 'u', 'password': 'secret-fictif'}, 'save')
        desk.db.close()
        reprise = self.desk()                                                                       # redémarrage : nouvelle connexion
        page = self.request('/guide-installation')['body']
        self.assertIn('data-session="%s"' % s['id'], page)
        self.assertIn('data-step="messagerie"', page)
        again = g.start(reprise, 'cabinet', mode='reprendre')
        self.assertEqual((again['id'], again['step']), (s['id'], 'messagerie'))
        self.assertEqual(again['values']['profile.nom_avocat'], 'MARTIN')
        self.assertEqual(again['secrets'], {'mail.password_file': 'configured'})
        self.assertNotIn('secret-fictif', json.dumps(again))

    def test_I06_session_d_une_version_anterieure_reprise_apres_migration(self):
        from agent import guide5631 as g
        desk = self.desk()
        desk.db.execute('DROP TABLE IF EXISTS guide_sessions5631')
        desk.db.execute('CREATE TABLE guide_sessions5631(id TEXT PRIMARY KEY, owner TEXT NOT NULL, revision INTEGER NOT NULL, base_config_revision INTEGER NOT NULL, '
                        'guide_version INTEGER NOT NULL, locale TEXT NOT NULL, features TEXT NOT NULL, step TEXT NOT NULL, status TEXT NOT NULL, '
                        'candidate TEXT NOT NULL, base TEXT NOT NULL, secret_refs TEXT NOT NULL, skipped TEXT NOT NULL, created TEXT NOT NULL, updated TEXT NOT NULL)')
        now = datetime.now(timezone.utc).isoformat()
        desk.db.execute('INSERT INTO guide_sessions5631 VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                        ('guide-ancienne', 'cabinet', 4, 0, 1, 'fr', '["veille"]', 'veille', 'in_progress', '{"profile.nom_avocat": "BERNARD"}', '{}', '{}', '[]', now, now))
        desk.db.commit()
        out = g.session(desk, 'cabinet', 'guide-ancienne')
        self.assertEqual((out['step'], out['mode'], out['later'], out['values']['profile.nom_avocat']), ('veille', 'nouveau', [], 'BERNARD'))
        g._schema(desk)                                                                            # migration rejouée : sans effet
        self.assertEqual(g.session(desk, 'cabinet', 'guide-ancienne')['revision'], 4)

    def test_I06_ajouter_une_fonction_sans_ressaisir_les_secrets_ni_rien_changer(self):
        from agent import assistant567, guide5631 as g
        desk, secret = self.installation_existante()
        avant_cfg = json.loads(self.config.read_text(encoding='utf-8'))
        avant_secret = Path(secret).read_bytes()
        avant_veille = veille.profile(desk)
        s = g.start(desk, 'cabinet', mode='ajouter')
        r = g.save_step(desk, 'cabinet', s['id'], s['revision'], 'fonctions', {'features': ['veille', 'agenda']})
        self.assertEqual(r['steps'], ['fonctions', 'agenda', 'veille', 'fin'])                     # ni profil, ni coordonnées, ni messagerie
        self.assertEqual(r['step'], 'agenda')
        r = g.save_step(desk, 'cabinet', s['id'], r['revision'], 'agenda', {}, 'skip')
        r = g.save_step(desk, 'cabinet', s['id'], r['revision'], 'veille', {}, 'continue')
        out = g.apply(desk, 'cabinet', s['id'], r['revision'], self.env)
        self.assertTrue(out['applied'])
        apres = json.loads(self.config.read_text(encoding='utf-8'))
        self.assertEqual(apres['installation']['features'], ['documents', 'messagerie', 'veille', 'agenda'])   # complétées, pas remplacées
        self.assertIn('documents', apres['installation']['partial'])
        for cle in ('installation',):
            avant_cfg.pop(cle, None)
            apres.pop(cle, None)
        self.assertEqual(apres, avant_cfg)                                                          # messagerie active, comptes inchangés
        self.assertIs(load_config(str(self.config))['mail']['enabled'], True)
        self.assertEqual(Path(secret).read_bytes(), avant_secret)                                   # secret intact, jamais redemandé
        self.assertEqual(veille.profile(self.desk()), avant_veille)                                 # fréquence explicite conservée
        self.assertEqual(assistant567.profile(self.desk())['avatar'], 'portrait_a')

    def test_I06_ajouter_la_messagerie_deja_configuree_garde_le_mot_de_passe(self):
        from agent import guide5631 as g
        desk, secret = self.installation_existante()
        avant = Path(secret).read_bytes()
        s = g.start(desk, 'cabinet', mode='ajouter')
        r = g.save_step(desk, 'cabinet', s['id'], s['revision'], 'fonctions', {'features': ['messagerie']})
        page = self.request('/guide-installation', query='session=%s&etape=messagerie' % s['id'])['body']
        self.assertNotIn('mot-de-passe-fictif-imap', page)                                         # le secret n'est jamais affiché
        r = g.save_step(desk, 'cabinet', s['id'], r['revision'], 'messagerie', {'host': 'mail.example.test', 'username': 'cabinet@example.test'}, 'continue')
        self.assertEqual(r['errors'], {})                                                           # aucun mot de passe exigé
        out = g.apply(desk, 'cabinet', s['id'], r['revision'], self.env)
        self.assertTrue(out['applied'], out)
        cfg = load_config(str(self.config))
        self.assertEqual((cfg['mail']['password_file'], cfg['mail']['enabled']), (secret, True))
        self.assertEqual(Path(secret).read_bytes(), avant)

    def test_I06_ajouter_propose_les_nouveautes_sans_toute_l_installation(self):
        from agent import guide5631 as g
        desk = self.desk()
        s = g.start(desk, 'cabinet', mode='ajouter')
        r = g.save_step(desk, 'cabinet', s['id'], s['revision'], 'fonctions', {'features': ['presentation', 'voix']})
        self.assertEqual(r['steps'], ['fonctions', 'profil', 'voix', 'fin'])
        n = g.start(desk, 'cabinet', ['presentation', 'documents'])                               # hors « ajouter » : nouveautés ignorées
        self.assertEqual(n['features'], ['documents'])

    def test_I06_etape_veille_reutilise_la_carte_sans_formulaire_imbrique_et_propose_trois_jours(self):
        from agent import guide5631 as g
        desk = self.desk()
        s = g.start(desk, 'cabinet', ['veille'])
        with patch('agent.common.HTTP', side_effect=AssertionError('aucun appel réseau')):
            page = self.request('/guide-installation', query='session=%s&etape=veille' % s['id'])['body']
        parser = Formulaires()
        parser.feed(page)
        self.assertEqual(parser.imbriques, 0)                                                       # aucun formulaire dans le formulaire du guide
        self.assertEqual([f.get('data-m5632') for f in parser.formulaires].count('veille/profile'), 1)
        self.assertEqual((parser.selection.get('mode'), parser.selection.get('interval_days')), ('interval', '3'))
        self.assertEqual(desk.db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0], 0)             # l'aperçu ne lance aucune collecte

    def test_I06_cartes_des_autres_lots_appelees_sinon_lien(self):
        from agent import guide5631 as g
        desk = self.desk()
        s = g.start(desk, 'cabinet', ['messagerie', 'documents'])
        ia = self.request('/guide-installation', query='session=%s&etape=ia' % s['id'])['body']
        self.assertEqual(ia.count('data-api="m5682/mistral/enregistrer"'), 1)                         # 5.6.96 (intégration) : vraie carte Mistral, une seule fois
        mail = self.request('/guide-installation', query='session=%s&etape=messagerie' % s['id'])['body']
        self.assertNotIn('<h3>Gmail</h3>', mail)                                                    # pas de connecteur Gmail livré
        appels = []
        carte = types.ModuleType('agent.mistral5696')
        carte.carte_guide = lambda desk, env, prefix, admin=False: appels.append(admin) or '<div id="carte-essai-ia"></div>'
        gmail = types.ModuleType('agent.gmail5697')
        gmail.carte_guide = lambda desk, env, prefix, admin=False: '<div id="carte-essai-gmail"></div>'
        with patch.dict(sys.modules, {'agent.mistral5696': carte, 'agent.gmail5697': gmail}):
            ia = self.request('/guide-installation', query='session=%s&etape=ia' % s['id'])['body']
            mail = self.request('/guide-installation', query='session=%s&etape=messagerie' % s['id'])['body']
        self.assertEqual(ia.count('id="carte-essai-ia"'), 1)
        self.assertEqual(appels, [True])                                                            # rôle transmis à la carte
        self.assertIn('id="carte-essai-gmail"', mail)

    def test_I06_presentation_salutation_du_compte_et_meteo_plus_tard(self):
        from agent import guide5631 as g
        desk = self.desk()
        desk.setting('assistant567:profile:cabinet', {'prenom': '<b>Claire</b>'})
        s = g.start(desk, 'cabinet', ['veille'])
        with patch('agent.common.HTTP', side_effect=AssertionError('aucun appel météo')):
            page = self.request('/guide-installation', query='session=%s&etape=profil' % s['id'])['body']
        salut = re.search(r'<p class="g5696-salutation">(.*?)</p>', page).group(1)
        self.assertTrue(salut.startswith('Bonjour &lt;b&gt;Claire&lt;/b&gt;, nous sommes le '), salut)
        self.assertIn('/static/axiorhub-icon.png', page)
        r = g.save_step(desk, 'cabinet', s['id'], s['revision'], 'profil', {'option': 'meteo'}, 'later')
        self.assertEqual((r['step'], r['later']), ('profil', ['meteo']))                            # reste sur l'étape, rappel noté
        with self.assertRaisesRegex(Stop, 'option_plus_tard_inconnue'):
            g.save_step(desk, 'cabinet', s['id'], r['revision'], 'profil', {'option': 'tout'}, 'later')
        r = g.save_step(desk, 'cabinet', s['id'], r['revision'], 'veille', {'option': 'veille'}, 'later')
        self.assertEqual(r['step'], 'voix')                                                         # « plus tard » sur l'étape : suivante
        rep = g.summary(desk, g._load(g._row(desk, s['id'], 'cabinet')))
        self.assertEqual(rep['plus_tard'], ['meteo', 'veille'])
        self.assertEqual(rep['etats']['veille'], 'plus_tard')

    def test_I06_recapitulatif_signale_refusee_incomplete_non_testee(self):
        from agent import guide5631 as g
        desk = self.desk()
        s = g.start(desk, 'cabinet', ['documents', 'messagerie', 'agenda', 'veille', 'facturation'])
        data = g._load(g._row(desk, s['id'], 'cabinet'))
        cfg = g.candidate_config(desk, data)
        g._record(desk, data, 'stockage', 'read', 'refused', 'http_401', 'refusé', {}, cfg)
        g._record(desk, data, 'messagerie', 'read', 'available', '', 'ok', {}, cfg)
        desk.db.commit()
        r = g.save_step(desk, 'cabinet', s['id'], s['revision'], 'facturation', {}, 'skip')
        rep = g.summary(desk, g._load(g._row(desk, s['id'], 'cabinet')))
        self.assertEqual(rep['etats'], {'documents': 'refusee', 'messagerie': 'incomplete', 'agenda': 'non_testee',
                                        'veille': 'a_completer', 'facturation': 'ignoree'})
        self.assertIn('veille', rep['missing'])                                                     # une veille non réglée n'est pas « disponible »
        page = self.request('/guide-installation', query='session=%s&etape=fin' % s['id'])['body']
        for etat in ('refusee', 'incomplete', 'non_testee', 'a_completer', 'ignoree'):
            self.assertIn('data-etat="%s"' % etat, page)
        self.assertIn('/parametres?rubrique=veille', page)
        self.assertNotIn('secret', json.dumps(rep).replace('secrets_included', ''))

    def test_I06_voix_apercu_sans_micro_ni_envoi(self):
        from agent import guide5631 as g
        desk = self.desk()
        s = g.start(desk, 'cabinet', ['veille'])
        page = self.request('/guide-installation', query='session=%s&etape=voix' % s['id'])['body']
        self.assertIn('data-g5696="micro"', page)                                                   # bouton explicite, rien d'automatique
        self.assertEqual(desk.db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0], 0)
        js = (t31.ROOT / 'agent' / 'static' / 'v5631.js').read_text(encoding='utf-8')
        appel = js.index('getUserMedia({ audio: true })')
        self.assertGreater(appel, js.index("mic.addEventListener('click'"))                       # sollicité dans le gestionnaire du clic


if __name__ == '__main__':
    unittest.main()
