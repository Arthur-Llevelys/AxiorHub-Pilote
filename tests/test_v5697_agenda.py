"""5.6.97, lot agenda : B05 (changement d'heure), B06 (un seul rendez-vous), § 14 (grille Jour et Semaine), § 14.1 (saisie), § 14.2 (Talk),
§ 14.3 (Speakr), § 15 (adaptateurs local, CalDAV, Google Agenda).

Données fictives (DURAND, MARTIN, DOS-001, DOS-002, example.test), faux services : faux serveur OCS Talk, fausse collection CalDAV, faux
Google Agenda. Toute connexion réseau non simulée fait échouer le test (``socket`` bloqué). Les dates sont relatives au jour ; les jours de
changement d'heure sont cherchés à partir d'aujourd'hui dans le fuseau du cabinet (Europe/Paris).
"""
from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path
import re
import socket
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit
from zoneinfo import ZoneInfo

from agent import agenda5634, agenda5697, agenda_web5697, calendriers5697 as cal, membres5650, rendezvous5697 as rdv
from agent.common import Stop, private_json
from agent.desk import Desk
from agent.erreurs5634 import http_error
import test_agent as fixtures

PARIS = ZoneInfo('Europe/Paris')
GOOGLE_HTTP_REEL = cal._google_http          # avant toute substitution par un faux Google
MOI = ('durand@example.test', 'avocat')
COLLEGUE = ('martin@example.test', 'avocat')
ADMIN = ('cabinet', 'administrateur')
MATTERS = [{'id': 'DOS-001', 'client_name': 'DURAND', 'path': '/Dossiers/DOS-001 - DURAND', 'references': ['DOS-001', 'RG 24/01234'], 'aliases': ['DURAND'],
            'correspondents': [{'email': 'durand@example.test', 'role': 'client'}]},
           {'id': 'DOS-002', 'client_name': 'MARTIN', 'path': '/Dossiers/DOS-002 - MARTIN', 'references': ['DOS-002'], 'aliases': ['MARTIN'],
            'correspondents': [{'email': 'martin@example.test', 'role': 'client'}]}]
MEMBRES = {o: {'owner': o, 'name': n, 'role': 'avocat'} for o, n in (('durand@example.test', 'Me DURAND'), ('martin@example.test', 'Me MARTIN'),
                                                                     ('bernard@example.test', 'Me BERNARD'))}


def changements_d_heure(zone=PARIS):
    """Prochains jours de passage à l'heure d'été (23 h) et à l'heure d'hiver (25 h), à partir d'aujourd'hui."""
    ete = hiver = None
    jour = date.today()
    for _ in range(400):
        a = datetime.combine(jour, datetime.min.time(), zone).utcoffset()
        b = datetime.combine(jour + timedelta(days=1), datetime.min.time(), zone).utcoffset()
        if b > a and ete is None:
            ete = jour
        if b < a and hiver is None:
            hiver = jour
        if ete and hiver:
            return ete, hiver
        jour += timedelta(days=1)
    raise AssertionError('aucun changement d’heure trouvé')


def mardi_prochain():
    d = date.today() + timedelta(days=1)
    while d.weekday() != 1:
        d += timedelta(days=1)
    return d


def sans_reseau(test):
    """Toute connexion réseau réelle fait échouer le test."""
    def interdit(*a, **k):
        raise AssertionError('appel réseau non simulé : %r' % (a[1:] if len(a) > 1 else a,))
    for p in (patch.object(socket.socket, 'connect', interdit), patch.object(socket, 'create_connection', interdit)):
        p.start()
        test.addCleanup(p.stop)


# ------------------------------------------------------------------------------------------------ faux services
class FauxOCS:
    """Faux serveur Talk OCS v4 : salons, participants, capacités ; peut perdre une réponse après avoir créé, ou refuser."""

    base = 'https://cloud.example.test'

    def __init__(self, compte='cabinet-nc'):
        self.compte, self.salons, self.appels, self.headers, self.last_headers = compte, {}, [], {}, {}
        self.perdre_creation = self.refus = False
        self.sans_talk = False
        self.n = 0

    def _ocs(self, data, code=200):
        return json.dumps({'ocs': {'meta': {'statuscode': code}, 'data': data}}).encode()

    def request(self, method, url, data=None, headers=None, limit=0):
        p = urlsplit(url)
        chemin = p.path
        self.appels.append((method, chemin))
        if chemin.endswith('/cloud/capabilities'):
            return self._ocs({'capabilities': {} if self.sans_talk else {'spreed': {'features': ['conversation-v4']}}})
        chemin = chemin.split('/apps/spreed/api/v4', 1)[1]
        champs = {k: v[0] for k, v in parse_qs((data or b'').decode()).items()}
        if self.refus:
            raise http_error(403, method, url)
        if method == 'GET' and chemin == '/room':
            return self._ocs([{'token': t, 'name': s['name']} for t, s in self.salons.items()])
        if method == 'POST' and chemin == '/room':
            self.n += 1
            jeton = 'jeton%04d' % self.n
            self.salons[jeton] = {'type': int(champs['roomType']), 'name': champs['roomName'], 'listable': 0, 'participants': [self.compte]}
            if self.perdre_creation:
                self.perdre_creation = False
                raise Stop('delai_http_depasse')
            return self._ocs({'token': jeton})
        m = re.fullmatch(r'/room/(\w+)(/participants)?', chemin)
        s = self.salons.get(m.group(1)) if m else None
        if s is None:
            raise http_error(404, method, url)
        if m.group(2) and method == 'POST':
            s['participants'].append(champs['newParticipant'])
            return self._ocs({})
        if m.group(2):
            return self._ocs([{'actorType': 'users', 'actorId': x} for x in s['participants']])
        return self._ocs({'type': s['type'], 'name': s['name'], 'listable': s['listable']})


def client_talk(ocs):
    from agent.talk568 import Talk
    t = Talk.__new__(Talk)
    t.cfg, t.http = {'username': ocs.compte}, ocs
    return rdv.SalonsAgenda({'username': ocs.compte}, talk=t)


class FauxCalDAV:
    """Fausse collection CalDAV : PUT conditionnel (If-None-Match, If-Match), GET avec ETag, PROPFIND ; perte de réponse simulable."""

    URL = 'https://dav.example.test/remote.php/dav/calendars/durand/cabinet/'

    def __init__(self):
        self.base, self.headers, self.last_headers, self.objets, self.puts, self.v = 'https://dav.example.test', {}, {}, {}, [], 0
        self.perdre_reponse = self.panne = False

    def request(self, method, url, data=None, headers=None, limit=0):
        headers = headers or {}
        if self.panne:
            raise Stop('connexion_http_indisponible')
        if method == 'PROPFIND':
            return (b'<d:multistatus xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav"><d:response><d:propstat><d:prop>'
                    b'<c:supported-calendar-component-set><c:comp name="VEVENT"/></c:supported-calendar-component-set>'
                    b'<d:current-user-privilege-set><d:privilege><d:read/></d:privilege><d:privilege><d:write/></d:privilege></d:current-user-privilege-set>'
                    b'</d:prop><d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response></d:multistatus>')
        if method == 'PUT':
            existe = url in self.objets
            if headers.get('If-None-Match') == '*' and existe:
                raise http_error(412, method, url)
            if 'If-Match' in headers and (not existe or self.objets[url][1] != headers['If-Match']):
                raise http_error(412, method, url)
            self.v += 1
            self.objets[url] = (data, '"v%d"' % self.v)
            self.puts.append((url, dict(headers)))
            if self.perdre_reponse:
                self.perdre_reponse = False
                raise Stop('delai_http_depasse')
            self.last_headers = {'ETag': '"v%d"' % self.v}
            return b''
        if method == 'GET':
            if url not in self.objets:
                raise http_error(404, method, url)
            self.last_headers = {'ETag': self.objets[url][1]}
            return self.objets[url][0]
        raise AssertionError('méthode inattendue ' + method)

    def modifier_a_distance(self, url, titre):
        """Un autre client modifie l'événement : nouvel ETag."""
        raw, _ = self.objets[url]
        self.v += 1
        self.objets[url] = (re.sub(rb'SUMMARY:[^\r\n]*', ('SUMMARY:' + titre).encode(), raw), '"v%d"' % self.v)


class FauxGoogle:
    """Faux Google Agenda v3 : insertion avec identifiant fourni (409 si pris), lecture, mise à jour conditionnelle (412), liste paginée."""

    def __init__(self):
        self.base, self.headers, self.last_headers, self.items, self.appels, self.v = cal.GOOGLE_API, {}, {}, {}, [], 0

    def request(self, method, url, data=None, headers=None, limit=0):
        p = urlsplit(url)
        q = {k: v[0] for k, v in parse_qs(p.query).items()}
        self.appels.append((method, p.path, q, json.loads(data) if data else None, dict(headers or {})))
        m = re.fullmatch(r'/calendar/v3/calendars/([^/]+)/events(?:/([^/]+))?', p.path)
        if p.path.startswith('/calendar/v3/users/me/calendarList/'):
            return json.dumps({'accessRole': 'owner'}).encode()
        ident = m.group(2)
        if method == 'POST':
            body = json.loads(data)
            if body['id'] in self.items:
                raise http_error(409, method, url)
            self.v += 1
            self.items[body['id']] = dict(body, etag='"g%d"' % self.v, status='confirmed')
            return json.dumps(self.items[body['id']]).encode()
        if method == 'GET' and ident:
            if ident not in self.items:
                raise http_error(404, method, url)
            return json.dumps(self.items[ident]).encode()
        if method == 'PUT':
            if self.items.get(ident, {}).get('etag') != (headers or {}).get('If-Match'):
                raise http_error(412, method, url)
            self.v += 1
            self.items[ident] = dict(json.loads(data), etag='"g%d"' % self.v, status='confirmed')
            return json.dumps(self.items[ident]).encode()
        if method == 'GET':
            return json.dumps({'items': list(self.items.values())}).encode()
        raise AssertionError(method)


# ------------------------------------------------------------------------------------------------ base
class Base(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.EngineTests('test_observation_has_no_mail_write')
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.f.c['calendar']['urls'] = []
        self.f.c['nextcloud'] = {'roots': ['/Dossiers'], 'url': 'https://cloud.example.test', 'username': 'cabinet-nc', 'password_file': '/coffre/essai'}
        private_json(Path(self.f.c['matters_file']), MATTERS)
        self.desk = Desk(self.f.c)
        self.addCleanup(self.desk.db.close)
        sans_reseau(self)
        p = patch('agent.audiences5688.members', side_effect=lambda desk, auth=None: dict(MEMBRES))
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(membres5650.clear)
        agenda5697.schema(self.desk)
        self.n = 0

    def cle(self):
        self.n += 1
        return 'cle-essai-agenda-%04d' % self.n

    def reserver(self, matter='DOS-002', comptes=('martin@example.test',)):
        membres5650.set_reserve(self.desk.c['state_dir'], matter, list(comptes))

    def creer(self, acteur=MOI, **data):
        jour = data.pop('jour', mardi_prochain())
        base = {'cle': self.cle(), 'titre': 'Rendez-vous DURAND', 'debut': '%sT10:00' % jour, 'fin': '%sT10:30' % jour}
        base.update(data)
        return agenda5697.creer(self.desk, acteur, base)

    def compte(self, table, where='1=1', args=()):
        return self.desk.db.execute('SELECT COUNT(*) FROM %s WHERE %s' % (table, where), args).fetchone()[0]


# ------------------------------------------------------------------------------------------------ B05
class ChangementDHeure(Base):
    def test_B05_heure_sautee_refusee_avec_horaire_valide_propose(self):
        ete, _ = changements_d_heure()
        avant = self.compte('agenda5634')
        with self.assertRaises(agenda5697.HoraireInvalide) as ctx:
            self.creer(debut='%sT02:30' % ete, fin='%sT03:30' % ete)
        self.assertEqual(str(ctx.exception), 'horaire_inexistant')
        self.assertEqual(ctx.exception.detail['proposition'], '%sT03:00' % ete)
        self.assertEqual(self.compte('agenda5634'), avant)                          # rien n'est écrit
        with self.assertRaises(Stop):                                                # ancienne route : même règle
            agenda5634.create(self.desk, {'title': 'Créneau sauté', 'start': '%sT02:30' % ete, 'duration_minutes': 30})
        self.assertEqual(self.compte('agenda5634'), avant)

    def test_B05_heure_repetee_les_deux_decalages_puis_choix(self):
        _, hiver = changements_d_heure()
        with self.assertRaises(agenda5697.HoraireInvalide) as ctx:
            self.creer(debut='%sT02:30' % hiver, fin='%sT03:30' % hiver)
        self.assertEqual(str(ctx.exception), 'horaire_ambigu')
        self.assertEqual([c['decalage'] for c in ctx.exception.detail['choix']], ['+02:00', '+01:00'])
        a = self.creer(debut='%sT02:30' % hiver, fin='%sT03:30' % hiver, decalage_debut='+02:00')
        b = self.creer(debut='%sT02:30' % hiver, fin='%sT03:30' % hiver, decalage_debut='+01:00')
        ra, rb = (self.desk.db.execute('SELECT starts,ends FROM agenda5634 WHERE id=?', (x['evenement_id'],)).fetchone() for x in (a, b))
        self.assertEqual(datetime.fromisoformat(ra['starts']), datetime.combine(hiver, datetime.min.time(), timezone.utc) + timedelta(minutes=30))
        self.assertEqual(datetime.fromisoformat(rb['starts']), datetime.combine(hiver, datetime.min.time(), timezone.utc) + timedelta(minutes=90))
        for r in (ra, rb):                                                           # aucune inversion : fin > début en UTC
            self.assertGreater(datetime.fromisoformat(r['ends']), datetime.fromisoformat(r['starts']))

    def test_B05_duree_ajoutee_a_l_instant_utc_sans_inversion_en_retour(self):
        _, hiver = changements_d_heure()
        ev = agenda5634.create(self.desk, {'title': 'Appel avant le retour à l’heure d’hiver', 'start': '%sT01:45' % hiver, 'duration_minutes': 60})['event']
        a, b = datetime.fromisoformat(ev['starts']), datetime.fromisoformat(ev['ends'])
        self.assertEqual(b - a, timedelta(minutes=60))                               # 60 minutes réelles, pas une heure murale
        self.assertGreater(b, a)
        with self.assertRaises(Stop) as ctx:                                         # fin murale avant le début : refusée
            self.creer(debut='%sT11:00' % mardi_prochain(), fin='%sT10:00' % mardi_prochain())
        self.assertEqual(str(ctx.exception), 'fin_avant_debut')

    def test_B05_journees_de_23_h_et_25_h_minuit_et_journee_entiere(self):
        ete, hiver = changements_d_heure()
        for jour, heures in ((ete, 23), (hiver, 25)):
            r = self.creer(titre='Journée entière %s' % jour, journee_entiere=True, debut=jour.isoformat(), jour_fin=(jour + timedelta(days=1)).isoformat(), fin='')
            row = self.desk.db.execute('SELECT starts,ends,all_day FROM agenda5634 WHERE id=?', (r['evenement_id'],)).fetchone()
            self.assertEqual(row['all_day'], 1)
            self.assertEqual(datetime.fromisoformat(row['ends']) - datetime.fromisoformat(row['starts']), timedelta(hours=heures))
            self.assertEqual(datetime.fromisoformat(row['starts']).astimezone(PARIS).date(), jour)         # paire de dates locales, fin exclusive
            self.assertEqual(datetime.fromisoformat(row['ends']).astimezone(PARIS).date(), jour + timedelta(days=1))
        j = mardi_prochain()
        r = self.creer(titre='Veille de minuit', debut='%sT23:00' % j, fin='%sT00:00' % (j + timedelta(days=1)))
        row = self.desk.db.execute('SELECT starts,ends FROM agenda5634 WHERE id=?', (r['evenement_id'],)).fetchone()
        self.assertEqual(datetime.fromisoformat(row['ends']) - datetime.fromisoformat(row['starts']), timedelta(hours=1))

    def test_B05_formulaire_des_agendas_en_ligne(self):
        from agent import agenda5625
        ete, hiver = changements_d_heure()
        with patch('agent.agenda5625._tz', return_value='Europe/Paris'):
            with self.assertRaises(agenda5697.HoraireInvalide):
                agenda5625.build_event(self.desk, 'durand@example.test', {'title': 'Audience fictive', 'date': ete.isoformat(), 'start': '02:30', 'end': '03:30'})
            with self.assertRaises(agenda5697.HoraireInvalide):
                agenda5625.build_event(self.desk, 'durand@example.test', {'title': 'Audience fictive', 'date': hiver.isoformat(), 'start': '02:30', 'end': '04:00'})
            ev, _, _ = agenda5625.build_event(self.desk, 'durand@example.test', {'title': 'Audience fictive', 'date': hiver.isoformat(), 'start': '02:30', 'end': '04:00',
                                                                                   'decalage_debut': '+01:00'})
            self.assertEqual(datetime.fromisoformat(ev['end']) - datetime.fromisoformat(ev['start']), timedelta(minutes=90))


# ------------------------------------------------------------------------------------------------ B06
class UnSeulRendezVous(Base):
    def test_B06_double_clic_et_perte_de_reponse_un_seul_rendez_vous(self):
        cle = self.cle()
        premier = self.creer(cle=cle)
        rejoue = self.creer(cle=cle)                                                 # double clic, ou réponse perdue après commit puis rejeu
        self.assertEqual(premier['evenement_id'], rejoue['evenement_id'])
        self.assertEqual((premier['http'], rejoue['http'], rejoue['rejeu']), (201, 200, True))
        self.assertEqual(self.compte('agenda5634', "title='Rendez-vous DURAND'"), 1)

    def test_B06_meme_cle_contenu_modifie_409_et_deux_creations_voulues(self):
        cle = self.cle()
        self.creer(cle=cle)
        with self.assertRaises(Stop) as ctx:
            self.creer(cle=cle, titre='Rendez-vous DURAND modifié')
        self.assertEqual(str(ctx.exception), 'cle_idempotence_deja_utilisee')
        self.assertEqual(agenda_web5697.STATUTS[str(ctx.exception)], '409 Conflict')
        self.creer()                                                                 # deuxième rendez-vous voulu : autre clé
        self.assertEqual(self.compte('agenda5634', "title='Rendez-vous DURAND'"), 2)

    def test_B06_coupure_avant_commit_rien_ni_cle_reservee(self):
        cle = self.cle()
        with patch('agent.agenda5634._mirror', side_effect=RuntimeError('coupure')):
            with self.assertRaises(RuntimeError):
                self.creer(cle=cle, objet='les_deux', tache={'echeance': ''})
        self.assertEqual((self.compte('agenda5634'), self.compte('tasks'), self.compte('agenda_ops5697')), (0, 0, 0))
        self.assertEqual(self.creer(cle=cle)['http'], 201)                           # la clé n'a pas été consommée

    def test_B06_cle_obligatoire_et_portee_par_acteur(self):
        with self.assertRaises(Stop) as ctx:
            agenda5697.creer(self.desk, MOI, {'titre': 'Sans clé', 'debut': '%sT10:00' % mardi_prochain()})
        self.assertEqual(str(ctx.exception), 'cle_idempotence_requise')
        cle = self.cle()
        a, b = self.creer(cle=cle), self.creer(acteur=COLLEGUE, cle=cle)            # portée : cabinet, acteur, commande, calendrier
        self.assertNotEqual(a['evenement_id'], b['evenement_id'])

    def test_B06_deux_sessions_conflit_de_version_explicite(self):
        r = self.creer()
        ev = agenda5697.evenement(self.desk, MOI, r['evenement_id'])
        agenda5697.modifier(self.desk, MOI, {'cle': self.cle(), 'id': ev['id'], 'version': ev['version'], 'titre': 'Session 1'})
        with self.assertRaises(Stop) as ctx:                                         # la session 2 a lu l'ancienne version
            agenda5697.modifier(self.desk, MOI, {'cle': self.cle(), 'id': ev['id'], 'version': ev['version'], 'titre': 'Session 2'})
        self.assertEqual(agenda_web5697.STATUTS[str(ctx.exception)], '409 Conflict')
        self.assertEqual(self.desk.db.execute('SELECT title FROM agenda5634 WHERE id=?', (ev['id'],)).fetchone()[0], 'Session 1')

    def test_B06_ecriture_conditionnelle_meme_sans_controle_prealable(self):
        """Mutation du contrôle préalable : la comparaison dans l'UPDATE suffit à refuser l'écrasement."""
        r = self.creer()
        ev = agenda5697.evenement(self.desk, MOI, r['evenement_id'])
        reel = agenda5634._row

        def lecture_perimee(desk, ident):   # la ligne a changé entre la lecture et l'écriture
            row = reel(desk, ident)
            desk.db.execute('UPDATE agenda5634 SET updated=? WHERE id=?', ('autre-session', ident))
            return row
        with patch('agent.agenda5634._row', side_effect=lecture_perimee):
            with self.assertRaises(Stop) as ctx:
                agenda5634.update(self.desk, {'id': ev['id'], 'title': 'Écrasement'})
        self.assertEqual(str(ctx.exception), 'evenement_modifie_entre_temps')
        self.assertNotEqual(self.desk.db.execute('SELECT title FROM agenda5634 WHERE id=?', (ev['id'],)).fetchone()[0], 'Écrasement')

    def test_B06_ancienne_route_du_panneau_cle_creee_a_l_ouverture(self):
        jour = mardi_prochain()
        data = {'cle': self.cle(), 'title': 'Rendez-vous MARTIN', 'start': '%sT14:00' % jour, 'duration_minutes': 60}
        a = agenda5634.api(self.desk, 'agenda/creer', dict(data), 'POST', 'durand@example.test')
        b = agenda5634.api(self.desk, 'agenda/creer', dict(data), 'POST', 'durand@example.test')
        self.assertEqual(a['id'], b['id'])
        self.assertEqual(self.compte('agenda5634', "title='Rendez-vous MARTIN'"), 1)
        with self.assertRaises(Stop):
            agenda5634.api(self.desk, 'agenda/creer', {k: v for k, v in data.items() if k != 'cle'}, 'POST', 'durand@example.test')
        from agent.web5634 import agenda_panel                                      # la clé est créée à l'ouverture du formulaire
        cles = re.findall(r'name="cle" value="([0-9a-f]{32})"', agenda_panel(self.desk, '/agent-courriel'))
        self.assertEqual(len(cles), 1)

    def test_B06_identite_distante_stable_au_rejeu(self):
        from agent import agenda5625
        a, _, _ = agenda5625.build_event(self.desk, 'durand@example.test', {'title': 'Audience', 'date': mardi_prochain().isoformat(), 'start': '09:00', 'cle': 'c' * 32})
        b, _, _ = agenda5625.build_event(self.desk, 'durand@example.test', {'title': 'Audience', 'date': mardi_prochain().isoformat(), 'start': '09:00', 'cle': 'c' * 32})
        self.assertEqual(a['key'], b['key'])
        self.assertEqual(cal.uid_stable('durand@example.test', 'cible', 'c' * 32), cal.uid_stable('durand@example.test', 'cible', 'c' * 32))


# ------------------------------------------------------------------------------------------------ § 14.1 saisie
class Saisie(Base):
    def test_A141_glisser_mardi_10h_12h_puis_relire(self):
        j = mardi_prochain()
        r = self.creer(debut='%sT10:00' % j, fin='%sT12:00' % j, nature='rendez_vous', dossier='DOS-001', lieu='Cabinet, salle 2')
        debut = datetime.combine(j, datetime.min.time(), PARIS)
        lus = agenda5697.locaux(self.desk, MOI, debut, debut + timedelta(days=1))   # relu comme après un rechargement
        self.assertEqual([(x['title'], x['nature'], x['matter']) for x in lus], [('Rendez-vous DURAND', 'rendez_vous', 'DOS-001')])
        ev = agenda5697.evenement(self.desk, MOI, r['evenement_id'])
        self.assertEqual((ev['debut_local'], ev['fin_local'], ev['location']), ('%sT10:00' % j, '%sT12:00' % j, 'Cabinet, salle 2'))

    def test_A141_tache_a_15h_par_clic_et_evenement_avec_tache_liee(self):
        j = mardi_prochain()
        t = self.creer(objet='tache', titre='Relire les conclusions', debut='%sT15:00' % j, fin='%sT15:30' % j, tache={'reserver': True, 'priorite': 'haute'})
        bloc = agenda5697.evenement(self.desk, MOI, t['evenement_id'])
        self.assertEqual((bloc['nature'], bloc['tache']['title'], bloc['tache']['priorite']), ('tache', 'Relire les conclusions', 'haute'))
        lie = self.creer(objet='les_deux', titre='Rendez-vous ZORVAL', tache={'echeance': (j + timedelta(days=3)).isoformat()})
        ev = agenda5697.evenement(self.desk, MOI, lie['evenement_id'])
        self.assertEqual(ev['tache']['id'], lie['tache_id'])                         # les deux objets s'ouvrent depuis la fiche
        self.assertEqual(self.desk.db.execute('SELECT evenement FROM taches5697 WHERE task_id=?', (lie['tache_id'],)).fetchone()[0], lie['evenement_id'])

    def test_A141_deplacer_le_bloc_ne_change_pas_l_echeance(self):
        j = mardi_prochain()
        echeance = (j + timedelta(days=5)).isoformat()
        r = self.creer(objet='tache', titre='Préparer le bordereau', tache={'reserver': True, 'echeance': echeance})
        ev = agenda5697.evenement(self.desk, MOI, r['evenement_id'])
        agenda5697.modifier(self.desk, MOI, {'cle': self.cle(), 'id': ev['id'], 'version': ev['version'], 'debut': '%sT16:00' % (j + timedelta(days=1)),
                                             'fin': '%sT17:00' % (j + timedelta(days=1))})
        self.assertEqual(self.desk.db.execute('SELECT due FROM tasks WHERE id=?', (r['tache_id'],)).fetchone()[0], echeance)
        self.assertEqual(agenda5697.evenement(self.desk, MOI, ev['id'])['debut_local'], '%sT16:00' % (j + timedelta(days=1)))

    def test_A141_tache_sans_bloc_reste_dans_la_liste(self):
        r = self.creer(objet='tache', titre='Appeler le greffe', tache={'reserver': False})
        self.assertEqual(r['evenement_id'], '')
        self.assertIn(r['tache_id'], [t['id'] for t in agenda5697.taches_non_programmees(self.desk, MOI)])
        prog = self.creer(titre='Appeler le greffe', nature='rendez_vous', tache_existante=r['tache_id'])   # « Programmer »
        self.assertNotIn(r['tache_id'], [t['id'] for t in agenda5697.taches_non_programmees(self.desk, MOI)])
        self.assertEqual(agenda5697.evenement(self.desk, MOI, prog['evenement_id'])['tache_id'], r['tache_id'])

    def test_A141_calendrier_en_lecture_seule_ou_desactive_rien_n_est_ecrit(self):
        with patch('agent.calendar568.effective_targets', return_value=[{'id': 'g1', 'label': 'Google DURAND', 'provider': 'google', 'config': {'calendar_id': 'primary'}, 'enabled': True}]):
            with self.assertRaises(Stop) as ctx:
                self.creer(calendrier='g1')
        self.assertEqual(str(ctx.exception), 'google_agenda_desactive')
        self.assertEqual((self.compte('agenda5634'), self.compte('agenda_sorties5697')), (0, 0))

    def test_A141_ia_en_pause_et_fournisseurs_bloques(self):
        from agent import arret5643
        arret5643.pause_all(self.desk, True)
        with patch('agent.model.Model.complete', side_effect=AssertionError('modèle appelé')), patch('agent.common.HTTP.request', side_effect=AssertionError('réseau')):
            r = self.creer(objet='les_deux', titre='Rendez-vous IA en pause', tache={})
            ev = agenda5697.evenement(self.desk, MOI, r['evenement_id'])
            agenda5697.modifier(self.desk, MOI, {'cle': self.cle(), 'id': ev['id'], 'version': ev['version'], 'lieu': 'Visio'})
        self.assertEqual(agenda5697.evenement(self.desk, MOI, ev['id'])['location'], 'Visio')

    def test_A141_serie_non_modifiable_ici(self):
        jour = mardi_prochain()
        ics = ('BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nUID:serie-essai@example.test\r\nDTSTART:%sT090000Z\r\nDTEND:%sT100000Z\r\nRRULE:FREQ=WEEKLY;COUNT=3\r\n'
               'SUMMARY:Point hebdomadaire\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n') % (jour.strftime('%Y%m%d'), jour.strftime('%Y%m%d'))
        agenda5634.import_ics(self.desk, ics)
        ident = self.desk.db.execute("SELECT id,updated FROM agenda5634 WHERE recurrence_id<>'' LIMIT 1").fetchone()
        with self.assertRaises(Stop) as ctx:
            agenda5697.modifier(self.desk, MOI, {'cle': self.cle(), 'id': ident['id'], 'version': ident['updated'], 'titre': 'Changé'})
        self.assertEqual(str(ctx.exception), 'edition_serie_non_prise_en_charge')


# ------------------------------------------------------------------------------------------------ droits
class Droits(Base):
    def test_A14_dossier_reserve_ni_lu_ni_cree_ni_compte_reponse_neutre(self):
        r = self.creer(acteur=COLLEGUE, dossier='DOS-002', titre='Rendez-vous réservé')
        self.reserver()
        j = mardi_prochain()
        debut = datetime.combine(j, datetime.min.time(), PARIS)
        self.assertEqual(agenda5697.locaux(self.desk, MOI, debut, debut + timedelta(days=1), limite=1), [])
        self.assertEqual(len(agenda5697.locaux(self.desk, COLLEGUE, debut, debut + timedelta(days=1))), 1)
        with self.assertRaises(Stop) as ctx:
            agenda5697.evenement(self.desk, MOI, r['evenement_id'])
        self.assertEqual(str(ctx.exception), 'introuvable')
        with self.assertRaises(Stop) as ctx:
            agenda5697.evenement(self.desk, MOI, 'inexistant')
        self.assertEqual(str(ctx.exception), 'introuvable')                          # même réponse : existence non divulguée
        with self.assertRaises(Stop) as ctx:
            self.creer(dossier='DOS-002')
        self.assertEqual(str(ctx.exception), 'introuvable')


# ------------------------------------------------------------------------------------------------ § 14 grille
class Grille(Base):
    def page(self, **args):
        from agent import grille5697
        membres5650.set_author('durand@example.test')
        membres5650.set_viewer_role('avocat')
        return grille5697.page(self.desk, {'view': 'week', **args}, lambda p, **kw: '/agent-courriel' + p + ('?' + '&'.join('%s=%s' % x for x in kw.items()) if kw else ''),
                               '/agent-courriel', 'personal')

    def test_A14_semaine_tous_les_creneaux_et_journee_entiere_cliquables(self):
        j = mardi_prochain()
        self.creer(debut='%sT10:00' % j, fin='%sT12:00' % j)
        html = self.page(date=j.isoformat())
        cases = re.findall(r'<button type="button" class="ag97-case ag97-c\d+ ag97-l2 ag97-r(\d+)[^"]*" data-jour="([\d-]+)" data-minute="(\d+)"', html)
        self.assertEqual(len(cases), 5 * 22)                                         # lundi-vendredi, 8 h-19 h par 30 min
        self.assertEqual(min(int(m) for _, _, m in cases), 8 * 60)
        self.assertEqual(max(int(m) for _, _, m in cases), 18 * 60 + 30)            # premier et dernier créneaux présents
        self.assertEqual(len(re.findall(r'class="ag97-case ag97-jcase', html)), 5)
        self.assertIn('ag97-rows22', html)
        ev = re.search(r'class="ag97-ev ag97-n-rendez_vous [^"]*ag97-r(\d+) ag97-s(\d+)"', html)
        self.assertEqual((int(ev.group(1)), int(ev.group(2))), (2 + 1 + 4, 4))       # 10 h = 5e créneau, 2 heures = 4 créneaux
        self.assertNotIn(' style=', html)                                            # CSP : aucun style en ligne
        self.assertIn('/static/v5697-agenda.js', html)

    def test_A14_hors_plage_signale_et_voir_toute_la_journee(self):
        j = mardi_prochain()
        self.creer(titre='Petit déjeuner', debut='%sT07:00' % j, fin='%sT07:30' % j)
        self.creer(titre='Dîner', debut='%sT20:00' % j, fin='%sT21:00' % j)
        html = self.page(date=j.isoformat())
        self.assertIn('▲ 1 avant 8 h', html)
        self.assertIn('▼ 1 après 19 h', html)
        self.assertIn('plage=journee', html)
        complet = self.page(date=j.isoformat(), view='day', plage='journee')
        self.assertEqual(len(re.findall(r'data-minute="\d+"', complet)), 48)
        self.assertIn('Petit déjeuner', complet)
        self.assertIn('Dîner', complet)

    def test_A14_chevauchements_regroupes_plus_n(self):
        j = mardi_prochain()
        for i in range(4):
            self.creer(titre='Réunion %d' % i, debut='%sT10:00' % j, fin='%sT11:00' % j)
        html = self.page(date=j.isoformat())
        self.assertIn('>+ 2</summary>', html)                                        # deux couloirs, deux regroupés
        self.assertEqual(len(re.findall(r'data-ag97-ev="[0-9a-f]{32}"', html)), 4)    # aucun masqué : tous restent ouvrables

    def test_A14_preferences_par_utilisateur(self):
        agenda5697.enregistrer_preferences(self.desk, 'durand@example.test', {'jours': '7', 'pas': '15', 'heure_debut': '9', 'heure_fin': '18'})
        html = self.page(date=mardi_prochain().isoformat())
        self.assertEqual(len(re.findall(r'data-minute="\d+"', html)), 7 * 36)
        self.assertEqual(agenda5697.preferences(self.desk, 'martin@example.test')['jours'], 5)   # jamais un réglage commun
        with self.assertRaises(Stop):
            agenda5697.enregistrer_preferences(self.desk, 'durand@example.test', {'heure_debut': '18', 'heure_fin': '9'})

    def test_A14_dossier_reserve_absent_de_la_grille_et_des_listes(self):
        self.creer(acteur=COLLEGUE, dossier='DOS-002', titre='Rendez-vous réservé MARTIN')
        self.reserver()
        html = self.page(date=mardi_prochain().isoformat())
        self.assertNotIn('Rendez-vous réservé MARTIN', html)
        self.assertNotIn('value="DOS-002"', html)                                     # ni dans la liste des dossiers de l'éditeur


# ------------------------------------------------------------------------------------------------ § 14.2 Talk
class Talk(Base):
    def setUp(self):
        super().setUp()
        self.ocs = FauxOCS()
        p = patch('agent.rendezvous5697._client', side_effect=lambda desk, client=None: client or client_talk(self.ocs))
        p.start()
        self.addCleanup(p.stop)

    def test_A142_desactive_par_defaut_rendez_vous_enregistre_avec_motif(self):
        r = self.creer(visio='talk', dossier='DOS-001')
        self.assertEqual(self.compte('agenda5634'), 1)
        self.assertEqual((r['salon']['etat'], r['salon']['lien']), ('indisponible', ''))
        self.assertIn('pas activés', r['salon']['motif'])
        self.assertEqual(self.ocs.appels, [])                                         # aucun appel quand Talk est désactivé

    def test_A142_ia_en_pause_un_evenement_et_un_salon_verifie(self):
        from agent import arret5643
        arret5643.pause_all(self.desk, True)
        rdv.regler_talk(self.desk, 'cabinet', True)
        with patch('agent.model.Model.complete', side_effect=AssertionError('modèle appelé')):
            r = self.creer(visio='talk', dossier='DOS-001', titre='Rendez-vous DURAND')
        self.assertEqual((r['http'], r['salon']['etat'], r['salon']['libelle']), (201, 'pret', 'Lien prêt'))
        salon = next(iter(self.ocs.salons.values()))
        self.assertEqual((salon['type'], salon['listable'], salon['participants']), (2, 0, ['cabinet-nc']))   # groupe privé, personne d'autre
        self.assertNotIn('DURAND', salon['name'])                                     # nom neutre
        self.assertTrue(r['salon']['lien'].startswith('https://cloud.example.test/call/'))
        self.assertFalse(r['salon']['invitation_envoyee'])

    def test_A142_perte_de_reponse_reconcilie_sans_second_salon_et_double_envoi(self):
        rdv.regler_talk(self.desk, 'cabinet', True)
        self.ocs.perdre_creation = True
        cle = self.cle()
        r = self.creer(cle=cle, visio='talk')
        self.assertEqual((r['http'], r['salon']['etat']), (202, 'incertain'))        # « Confirmation inconnue », jamais « Lien prêt »
        self.assertEqual(r['salon']['lien'], '')
        self.assertEqual(self.creer(cle=cle, visio='talk')['evenement_id'], r['evenement_id'])   # double envoi : même événement
        repris = rdv.relancer(self.desk, MOI, r['evenement_id'])
        self.assertEqual(repris['etat'], 'pret')
        self.assertEqual(len(self.ocs.salons), 1)                                     # réconcilié par le nom, pas recréé
        self.assertEqual(sum(1 for m, c in self.ocs.appels if m == 'POST' and c.endswith('/room')), 1)

    def test_A142_refus_ocs_et_talk_absent_rendez_vous_garde(self):
        rdv.regler_talk(self.desk, 'cabinet', True)
        self.ocs.refus = True
        r = self.creer(visio='talk')
        self.assertEqual((r['salon']['etat'], r['salon']['lien']), ('refuse', ''))
        self.assertIn('refusé', r['salon']['motif'])
        self.ocs.refus, self.ocs.sans_talk = False, True
        r2 = self.creer(visio='talk')
        self.assertEqual(r2['salon']['etat'], 'indisponible')
        self.assertEqual(self.compte('agenda5634'), 2)

    def test_A142_salon_relu_non_prive_lien_jamais_affiche(self):
        rdv.regler_talk(self.desk, 'cabinet', True)
        reel = self.ocs.request

        def listable(method, url, data=None, headers=None, limit=0):   # l'instance rend un salon ouvert à tous les comptes
            out = reel(method, url, data, headers, limit)
            for s in self.ocs.salons.values():
                s['listable'] = 1
            return out
        self.ocs.request = listable
        r = self.creer(visio='talk')
        self.assertEqual((r['salon']['etat'], r['salon']['lien']), ('refuse', ''))
        self.assertIn('pas privé', r['salon']['motif'])

    def test_A142_participants_apres_choix_explicite_et_acces_au_dossier(self):
        rdv.regler_talk(self.desk, 'cabinet', True)
        r = self.creer(visio='talk', dossier='DOS-001')
        with self.assertRaises(Stop):
            rdv.ajouter_participants(self.desk, MOI, r['evenement_id'], ['martin@example.test'])            # sans confirmation
        with self.assertRaises(Stop):
            rdv.ajouter_participants(self.desk, MOI, r['evenement_id'], ['inconnu@example.test'], True)     # pas un membre identifié
        self.reserver('DOS-001', ('durand@example.test',))
        with self.assertRaises(Stop):
            rdv.ajouter_participants(self.desk, MOI, r['evenement_id'], ['martin@example.test'], True)      # sans accès au dossier
        membres5650.set_reserve(self.desk.c['state_dir'], 'DOS-001', [])
        out = rdv.ajouter_participants(self.desk, MOI, r['evenement_id'], ['martin@example.test'], True)
        self.assertEqual(out['participants'], ['martin@example.test'])
        self.assertIn('aucune invitation calendrier ni courriel', out['message'])
        self.assertEqual(sorted(next(iter(self.ocs.salons.values()))['participants']), ['cabinet-nc', 'martin@example.test'])

    def test_A142_lien_seulement_pour_les_personnes_autorisees_et_deplacement_conserve(self):
        rdv.regler_talk(self.desk, 'cabinet', True)
        r = self.creer(acteur=COLLEGUE, visio='talk', dossier='DOS-002')
        self.reserver()
        self.assertIsNone(rdv.etat_public(self.desk, MOI, r['evenement_id']))       # compte non autorisé : rien, pas même l'état
        ev = agenda5697.evenement(self.desk, COLLEGUE, r['evenement_id'])
        lien = ev['salon']['lien']
        agenda5697.modifier(self.desk, COLLEGUE, {'cle': self.cle(), 'id': ev['id'], 'version': ev['version'], 'debut': '%sT15:00' % mardi_prochain(),
                                                  'fin': '%sT16:00' % mardi_prochain()})
        self.assertEqual(agenda5697.evenement(self.desk, COLLEGUE, ev['id'])['salon']['lien'], lien)   # déplacé : même salon
        self.assertNotIn(lien.rsplit('/', 1)[-1], agenda5634.export_ics(self.desk))  # jamais dans un export


# ------------------------------------------------------------------------------------------------ § 14.3 Speakr
class Speakr(Base):
    def test_A143_adresse_validee_sans_identifiant_ni_jeton(self):
        self.assertEqual(rdv.speakr_valider('https://speakr.example.test'), 'https://speakr.example.test/')
        for mauvais in ('http://speakr.example.test', 'https://jean:secret@speakr.example.test', 'https://speakr.example.test/?token=abc',
                        'https://speakr.example.test/#x', 'javascript:alert(1)', 'ftp://speakr.example.test'):
            with self.assertRaises(Stop, msg=mauvais):
                rdv.speakr_valider(mauvais)
        self.assertEqual(rdv.speakr_valider('http://127.0.0.1:8899'), 'http://127.0.0.1:8899/')   # boucle locale d'essai seulement

    def test_A143_lien_nouvel_onglet_sans_referent_ni_titre_ni_dossier(self):
        rdv.speakr_regler(self.desk, ADMIN, 'cabinet', 'https://speakr.example.test')
        r = self.creer(speakr=True, dossier='DOS-001', titre='Rendez-vous DURAND')
        ev = agenda5697.evenement(self.desk, MOI, r['evenement_id'])
        self.assertEqual(ev['speakr_url'], 'https://speakr.example.test/')         # adresse seule : ni titre ni dossier
        lien = rdv.speakr_lien_html(self.desk, 'durand@example.test')
        self.assertIn('target="_blank"', lien)
        self.assertIn('rel="noopener noreferrer"', lien)
        self.assertNotIn('DURAND', lien)

    def test_A143_adresse_personnelle_retrait_et_cabinet_reserve_a_l_administrateur(self):
        with self.assertRaises(Stop) as ctx:
            rdv.speakr_regler(self.desk, MOI, 'cabinet', 'https://speakr.example.test')
        self.assertEqual(str(ctx.exception), 'role_insuffisant')
        rdv.speakr_regler(self.desk, MOI, 'personnel', 'https://perso.example.test/speakr')
        self.assertEqual(rdv.speakr_adresse(self.desk, 'durand@example.test'), 'https://perso.example.test/speakr/')
        self.assertEqual(rdv.speakr_adresse(self.desk, 'martin@example.test'), '')    # aucun lien d'exemple par défaut
        rdv.speakr_regler(self.desk, MOI, 'personnel', '')
        self.assertEqual(rdv.speakr_adresse(self.desk, 'durand@example.test'), '')
        self.assertEqual(rdv.speakr_lien_html(self.desk, 'durand@example.test'), '')

    def test_A143_aucune_capture_audio_ni_adresse_d_exemple(self):
        racine = Path(__file__).resolve().parents[1] / 'agent'
        for fichier in ('static/v5697-agenda.js', 'rendezvous5697.py', 'grille5697.py'):
            texte = (racine / fichier).read_text(encoding='utf-8')
            self.assertNotRegex(texte, r'getUserMedia|MediaRecorder|getDisplayMedia|AudioContext', fichier)
        for fichier in ('guide.py', 'guided_ui.py'):
            self.assertNotIn('speakr.example.com', (racine / fichier).read_text(encoding='utf-8'))


# ------------------------------------------------------------------------------------------------ § 15 adaptateurs
class Adaptateurs(Base):
    CIBLE = {'id': 'cd1', 'label': 'Cabinet (CalDAV)', 'provider': 'caldav', 'config': {'url': FauxCalDAV.URL}, 'enabled': True}
    GOOGLE = {'id': 'g1', 'label': 'Google DURAND', 'provider': 'google', 'config': {'calendar_id': 'agenda-durand@example.test'}, 'enabled': True}

    def setUp(self):
        super().setUp()
        self.dav, self.google = FauxCalDAV(), FauxGoogle()
        for p in (patch('agent.calendar568.effective_targets', return_value=[self.CIBLE, self.GOOGLE]),
                  patch('agent.calendar568._http', return_value=self.dav), patch('agent.calendriers5697._google_http', side_effect=self._google),
                  patch('agent.calendriers5697._google_connecte', return_value=True)):
            p.start()
            self.addCleanup(p.stop)

    def _google(self, desk, owner):
        if not cal.google_reglage(desk)['actif']:
            raise Stop('google_agenda_desactive')
        return self.google

    def test_A15_local_sans_reseau_aucune_ecriture_distante(self):
        r = self.creer()
        self.assertEqual((r['http'], r['sortie']), (201, ''))
        self.assertEqual((self.dav.puts, self.compte('agenda_sorties5697')), ([], 0))

    def test_A15_caldav_creation_if_none_match_puis_relecture(self):
        r = self.creer(calendrier='cd1', titre='Rendez-vous CalDAV')
        self.assertEqual((r['http'], r['synchronisation']['etat']), (201, 'confirme'))
        self.assertEqual(self.compte('agenda5634'), 0)                                # rien de local : une destination, un résultat
        url, entetes = self.dav.puts[0]
        self.assertEqual(entetes.get('If-None-Match'), '*')
        self.assertTrue(url.startswith(FauxCalDAV.URL + 'ag97'))
        self.assertNotIn(b'ATTENDEE', self.dav.objets[url][0])                       # aucune invitation
        self.assertEqual(cal.recu(self.desk, r['sortie'])['distant']['etag'], '"v1"')

    def test_A15_caldav_perte_de_reponse_relecture_avant_relance(self):
        self.dav.perdre_reponse = True
        cle = self.cle()
        r = self.creer(cle=cle, calendrier='cd1')
        self.assertEqual((r['http'], r['synchronisation']['etat']), (202, 'confirmation_inconnue'))
        self.assertEqual(self.creer(cle=cle, calendrier='cd1')['sortie'], r['sortie'])   # rejeu : même commande, pas de nouvelle écriture
        etat = cal.executer(self.desk, r['sortie'])
        self.assertEqual(etat['etat'], 'confirme')
        self.assertEqual(len(self.dav.puts), 1)                                       # relu, pas réécrit

    def test_A15_caldav_conflit_412_montre(self):
        r = self.creer(calendrier='cd1', titre='Rendez-vous initial')
        recu = cal.recu(self.desk, r['sortie'])
        url = FauxCalDAV.URL + recu['uid'] + '.ics'
        self.dav.modifier_a_distance(url, 'Changé ailleurs')
        charge = dict(json.loads(self.desk.db.execute('SELECT charge FROM agenda_sorties5697 WHERE id=?', (r['sortie'],)).fetchone()[0]), titre='Ma version')
        s = cal.modifier_distant(self.desk, MOI, 'cd1', recu['uid'], recu['distant']['etag'], charge, self.cle())
        etat = cal.executer(self.desk, s)
        self.assertEqual(etat['etat'], 'conflit')
        self.assertEqual(etat['distant']['titre'], 'Changé ailleurs')                  # la version distante est montrée
        self.assertIn('Changé ailleurs', self.dav.objets[url][0].decode('utf-8'))       # rien n'est écrasé

    def test_A15_caldav_capacites_et_panne_cache_perime_agenda_personnel_utilisable(self):
        adaptateur = cal.CalDAV(self.dav, FauxCalDAV.URL)
        self.assertEqual(adaptateur.capacites(), {'vevent': True, 'vtodo': False, 'ecriture': True, 'lecture': True})
        self.dav.panne = True
        j = mardi_prochain()
        debut = datetime.combine(j, datetime.min.time(), PARIS)
        _, etat = cal.lire_cible(self.desk, MOI, 'cd1', debut, debut + timedelta(days=1))
        self.assertEqual(etat['etat'], 'erreur')                                      # jamais lu : erreur affichée, pas un faux zéro
        self.assertIn('Lecture impossible', etat['erreur'])
        self.creer()
        self.assertEqual(len(agenda5697.locaux(self.desk, MOI, debut, debut + timedelta(days=1))), 1)

    def test_A15_essais_bornes_puis_action_utilisateur(self):
        self.dav.panne = True
        r = self.creer(calendrier='cd1')
        for _ in range(cal.ESSAIS + 1):
            etat = cal.executer(self.desk, r['sortie'])
        self.assertEqual(etat['etat'], 'abandon')
        self.assertEqual(etat['tentatives'], cal.ESSAIS)

    def test_A15_google_desactive_par_defaut(self):
        self.assertEqual(next(s for s in cal.sources(self.desk, 'durand@example.test') if s['id'] == 'g1')['etat'], 'desactive')
        with self.assertRaises(Stop):
            self.creer(calendrier='g1')
        self.assertEqual(self.google.appels, [])

    def test_A15_google_identifiant_stable_sans_invite_relu_puis_conflit_412(self):
        cal.regler_google(self.desk, 'cabinet', True)
        r = self.creer(calendrier='g1', titre='Rendez-vous Google')
        self.assertEqual(r['synchronisation']['etat'], 'confirme')
        methode, chemin, q, corps, _ = next(a for a in self.google.appels if a[0] == 'POST')
        self.assertRegex(corps['id'], r'^[0-9a-v]{5,1024}$')                           # base32hex conforme
        self.assertEqual(q.get('sendUpdates'), 'none')
        self.assertNotIn('attendees', corps)
        recu = cal.recu(self.desk, r['sortie'])
        self.google.items[recu['uid']]['etag'] = '"autre"'                             # modifié ailleurs
        s = cal.modifier_distant(self.desk, MOI, 'g1', recu['uid'], recu['distant']['etag'], dict(json.loads(
            self.desk.db.execute('SELECT charge FROM agenda_sorties5697 WHERE id=?', (r['sortie'],)).fetchone()[0]), titre='Autre'), self.cle())
        self.assertEqual(cal.executer(self.desk, s)['etat'], 'conflit')
        self.assertEqual([a[4].get('If-Match') for a in self.google.appels if a[0] == 'PUT'], [recu['distant']['etag']])

    def test_A15_google_lecture_dans_la_meme_plage(self):
        cal.regler_google(self.desk, 'cabinet', True)
        self.creer(calendrier='g1', titre='Rendez-vous Google lu')
        j = mardi_prochain()
        debut = datetime.combine(j, datetime.min.time(), PARIS)
        rows, etat = cal.lire_cible(self.desk, MOI, 'g1', debut, debut + timedelta(days=1))
        self.assertEqual(([x['title'] for x in rows], etat['etat']), (['Rendez-vous Google lu'], 'a_jour'))
        liste = next(a for a in self.google.appels if a[0] == 'GET' and a[1].endswith('/events') and 'timeMin' in a[2])
        self.assertEqual(liste[2]['singleEvents'], 'true')

    def test_A15_google_par_google5697_capacite_par_operation_et_codes_d_erreur(self):
        from agent import google5697
        cal.regler_google(self.desk, 'cabinet', True)
        with self.assertRaises(Stop) as ctx:                                          # pas de connexion « agenda » : refus explicite
            GOOGLE_HTTP_REEL(self.desk, 'durand@example.test')
        self.assertEqual(str(ctx.exception), 'google_agenda_non_connecte')
        appels = []

        def appeler(desk, conn_id, methode, url, corps=None, capacite='', entetes=None):
            appels.append((methode, capacite, entetes))
            if methode == 'PUT':
                raise google5697.ErreurGoogle('google_http_412', 412)
            return {'id': 'x', 'etag': '"e"'}
        pont = cal.PontGoogle(self.desk, 'conn-essai')
        with patch('agent.google5697.appeler', side_effect=appeler):
            pont.request('GET', cal.GOOGLE_API + '/calendars/c/events/x')
            with self.assertRaises(Stop) as ctx:
                pont.request('PUT', cal.GOOGLE_API + '/calendars/c/events/x', b'{}', {'If-Match': '"e"', 'Content-Type': 'application/json'})
        self.assertEqual(str(ctx.exception), 'http_412')
        self.assertEqual([(m, c) for m, c, _ in appels], [('GET', 'agenda.lecture'), ('PUT', 'agenda.ecriture')])
        self.assertEqual(appels[1][2], {'If-Match': '"e"'})

    def test_A15_reseau_reel_interdit_dans_les_tests(self):
        with self.assertRaises(AssertionError):
            socket.create_connection(('192.0.2.1', 443), timeout=0.01)


class Retention(Base):
    def test_A15_retention_des_operations_export_et_cycle_de_surveillance(self):
        self.creer()
        ancien = (datetime.now(timezone.utc) - timedelta(days=agenda5697.RETENTION_OPERATIONS + 1)).isoformat()
        self.creer()
        self.desk.db.execute('UPDATE agenda_ops5697 SET created=? WHERE rowid=(SELECT MIN(rowid) FROM agenda_ops5697)', (ancien,))
        self.desk.db.commit()
        self.assertEqual(agenda5697.purger(self.desk)['operations'], 1)
        self.assertEqual(self.compte('agenda_ops5697'), 1)
        self.assertEqual(self.compte('agenda5634'), 2)                                 # seules les lignes techniques expirent
        from agent import export5688
        tables = {t[2] for t in export5688.TABLES}
        for t in ('agenda5697', 'agenda_ops5697', 'agenda_salons5697', 'agenda_sorties5697', 'agenda_sources5697', 'taches5697'):
            self.assertIn(t, tables)
        with patch('agent.live430.emit'):
            self.assertEqual(agenda5634.tick(self.desk)['sorties'], [])               # rien à reprendre, aucun appel


# ------------------------------------------------------------------------------------------------ API m5689/agenda
class Routes(Base):
    def env(self, method='GET', corps=None, qs='', acteur=MOI):
        import io
        brut = json.dumps(corps or {}).encode()
        return {'REQUEST_METHOD': method, 'QUERY_STRING': qs, 'HTTP_ORIGIN': 'https://cabinet.example.test', 'HTTP_X_CSRF_TOKEN': 'jeton-csrf-essai',
                'CONTENT_TYPE': 'application/json', 'CONTENT_LENGTH': str(len(brut)), 'wsgi.input': io.BytesIO(brut),
                'HTTP_X_AXIORHUB_USER': acteur[0], 'HTTP_X_AXIORHUB_ROLE': acteur[1]}

    def appel(self, nom, method='GET', corps=None, args=None, acteur=MOI):
        auth = {'origin': 'https://cabinet.example.test', 'csrf': 'jeton-csrf-essai'}
        r = agenda_web5697.api(self.env(method, corps, acteur=acteur), self.desk, auth, '/agent-courriel', 'm5689/' + nom, args or {}, method)
        return r['status'], json.loads(r['body'])

    def test_A15_post_201_rejeu_200_get_par_source_et_404_neutre(self):
        j = mardi_prochain()
        corps = {'cle': self.cle(), 'titre': 'Rendez-vous API', 'debut': '%sT09:00' % j, 'fin': '%sT09:30' % j}
        s1, r1 = self.appel('agenda/evenements', 'POST', corps)
        s2, r2 = self.appel('agenda/evenements', 'POST', corps)
        self.assertEqual((s1, s2, r1['evenement_id']), ('201 Created', '200 OK', r2['evenement_id']))
        s, lu = self.appel('agenda', args={'debut': '%sT00:00' % j, 'fin': '%sT00:00' % (j + timedelta(days=1)), 'calendriers': 'local'})
        self.assertEqual([x['title'] for x in lu['sources'][0]['evenements']], ['Rendez-vous API'])
        self.reserver('DOS-002')
        _, r3 = self.appel('agenda/evenements', 'POST', dict(corps, cle=self.cle(), dossier='DOS-002'), acteur=COLLEGUE)
        s4, r4 = self.appel('agenda/evenement', args={'id': r3['evenement_id']})
        self.assertEqual((s4, r4['error']), ('404 Not Found', 'introuvable'))

    def test_A16_fiche_du_rendez_vous_nouvelle_demande(self):
        r = self.creer(nature='rendez_vous')
        _, d = self.appel('agenda/evenement', args={'id': r['evenement_id']})
        self.assertEqual(d['nouvelle_demande']['libelle'], 'Nouvelle demande')
        q = parse_qs(urlsplit(d['nouvelle_demande']['href']).query)
        self.assertEqual((q['origine'], q['ref']), (['rendez_vous'], [r['evenement_id']]))
        repas = self.creer(nature='repas')
        self.assertNotIn('nouvelle_demande', self.appel('agenda/evenement', args={'id': repas['evenement_id']})[1])

    def test_B05_api_422_avec_les_deux_decalages(self):
        _, hiver = changements_d_heure()
        s, r = self.appel('agenda/evenements', 'POST', {'cle': self.cle(), 'titre': 'Heure répétée', 'debut': '%sT02:30' % hiver, 'fin': '%sT03:30' % hiver})
        self.assertEqual((s, r['error'], len(r['detail']['choix'])), ('422 Unprocessable Entity', 'horaire_ambigu', 2))

    def test_A14_routes_reservees_et_csrf(self):
        s, _ = self.appel('agenda/reglages/talk', 'POST', {'actif': True})
        self.assertEqual(s, '403 Forbidden')                                         # administrateur seulement
        self.assertFalse(rdv.talk_reglage(self.desk)['actif'])
        s, _ = self.appel('agenda/reglages/talk', 'POST', {'actif': True}, acteur=ADMIN)
        self.assertEqual(s, '200 OK')
        env = self.env('POST', {'cle': self.cle(), 'titre': 'x'})
        env['HTTP_X_CSRF_TOKEN'] = 'mauvais'
        r = agenda_web5697.api(env, self.desk, {'origin': 'https://cabinet.example.test', 'csrf': 'jeton-csrf-essai'}, '', 'm5689/agenda/evenements', {}, 'POST')
        self.assertEqual(r['status'], '403 Forbidden')
        self.assertEqual(membres5650.category('POST', '/api440/m5689/agenda/evenements'), 'agenda')
        self.assertEqual(membres5650.category('POST', '/api440/m5689/agenda/reglages/google'), 'reglages')


if __name__ == '__main__':
    unittest.main()
