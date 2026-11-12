"""5.6.97, lot gmail (cahier v3 C03 à C07) : client OAuth Google commun, fournisseur Gmail derrière le contrat de boîte de la 5.6.96,
projet local, brouillon dans le fil, « Envoyer maintenant » après relecture exacte, et non-régression IMAP.

Faux Google au niveau du transport (``google5697.TRANSPORT``) : jeton, révocation, profil, messages, historique, pièces, brouillons et
envoi, au format de la documentation publique (échantillons écrits à la main dans ``tests/fixtures/gmail5697``). Tout autre appel réseau
fait échouer le test (``common.HTTP.request`` bloqué). Aucun identifiant réel ; données fictives seulement (DURAND, MARTIN, BERNARD,
KERVILAC, Valbrune, example.test). Dates relatives au jour."""
import base64
from datetime import datetime, timedelta, timezone
from email import policy
from email.parser import BytesParser
import hashlib
import io
import json
from pathlib import Path
import time
import unittest
import uuid
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from agent import google5697 as gg, gmail5697 as gm, membres5650
from agent.common import Stop
from agent.desk import Desk
import test_agent as fixtures

CLIENT_ID = 'client-web-fictif-0001.example.test'
SECRET = 'secret-client-fictif-0001'
RETOUR = 'https://pilote.example.test/p/api440/m5689/oauth/google/callback'
API = 'https://gmail.googleapis.com/gmail/v1/users/me'
LECTURE, BROUILLONS = gg.PORTEES['gmail.lecture'], gg.PORTEES['gmail.brouillons']
CLAIRE, CAMILLE = 'claire@example.test', 'camille@example.test'
GMAIL_CLAIRE, GMAIL_CAMILLE = 'claire.gmail@example.test', 'camille.gmail@example.test'
ORIGINE, JETON = 'https://cabinet.example.test', 'jeton-csrf-fictif'
FIXTURES = Path(__file__).parent / 'fixtures' / 'gmail5697'


def b64(octets):
    return base64.urlsafe_b64encode(octets).decode().rstrip('=')


def deb64(texte):
    return base64.urlsafe_b64decode(texte + '=' * (-len(texte) % 4))


class Reponse(Exception):
    """Réponse imposée par une panne : (statut, en-têtes, corps)."""


class FauxGoogle:
    """Google en mémoire : OAuth (code + PKCE S256, renouvellement, révocation) et API Gmail. Chaque appel est noté ; les jetons d'accès
    sont vérifiés à chaque requête Gmail (401 sinon), les portées aussi (403)."""

    def __init__(self):
        self.appels, self.pannes, self.codes, self.acces, self.renouvellements = [], [], {}, {}, {}
        self.comptes = {}                    # jeton de renouvellement -> adresse
        self.boites = {}                     # adresse -> {'messages', 'histoire', 'history_id', 'brouillons', 'envoyes'}
        self.revocations, self.n = [], 0
        self.histoire_404, self.pendant_renouvellement = False, None

    # -------------------------------------------------------------------------------- outils des tests
    def boite(self, adresse):
        return self.boites.setdefault(adresse, {'messages': {}, 'histoire': [], 'history_id': 1000, 'brouillons': {}, 'envoyes': {}})

    def consentir(self, url, adresse=GMAIL_CLAIRE, accordees=None):
        """La personne choisit son compte et consent : rend le code (portées accordées : demandées, sauf ``accordees``)."""
        q = {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}
        self.n += 1
        code = 'code-%d' % self.n
        portees = set(q['scope'].split()) if accordees is None else set(accordees)
        if q.get('include_granted_scopes') == 'true':
            portees |= {p for r, a in self.comptes.items() if a == adresse for p in self.renouvellements.get(r, {}).get('portees', ())}
        self.codes[code] = {'defi': q['code_challenge'], 'methode': q['code_challenge_method'], 'redirect': q['redirect_uri'], 'adresse': adresse,
                            'portees': sorted(portees), 'client': q['client_id']}
        return {'state': q['state'], 'code': code}

    def message(self, adresse, de, objet, corps='Bonjour Maître.', jours=0, fil=None, etiquettes=('INBOX', 'UNREAD'), entetes=None, piece=None, html=None, cc=''):
        b = self.boite(adresse)
        self.n += 1
        gid = '%016x' % (0x18c0000000000000 + self.n)
        mid = '<m%d@example.test>' % self.n
        heads = [{'name': 'From', 'value': de}, {'name': 'To', 'value': adresse}, {'name': 'Subject', 'value': objet}, {'name': 'Message-ID', 'value': mid}]
        if cc:
            heads.append({'name': 'Cc', 'value': cc})
        heads += [{'name': k, 'value': v} for k, v in (entetes or {}).items()]
        parties = [{'partId': '0', 'mimeType': 'text/html' if html else 'text/plain', 'filename': '', 'headers': [],
                    'body': {'size': len((html or corps).encode()), 'data': b64((html or corps).encode())}}]
        if piece:
            parties.append({'partId': '1', 'mimeType': 'text/plain', 'filename': piece[0], 'headers': [], 'body': {'attachmentId': 'att-%d' % self.n, 'size': len(piece[1])}})
            b.setdefault('pieces', {})['att-%d' % self.n] = piece[1]
        quand = datetime.now(timezone.utc) + timedelta(days=jours)
        b['history_id'] += 1
        m = {'id': gid, 'threadId': fil or gid, 'labelIds': list(etiquettes), 'historyId': str(b['history_id']), 'internalDate': str(int(quand.timestamp() * 1000)),
             'payload': {'mimeType': 'multipart/mixed', 'headers': heads, 'body': {'size': 0}, 'parts': parties}, '_mid': mid}
        b['messages'][gid] = m
        b['histoire'].append({'id': str(b['history_id']), 'messagesAdded': [{'message': {'id': gid, 'threadId': m['threadId'], 'labelIds': list(etiquettes)}}]})
        return gid, m['threadId'], mid

    def supprimer(self, adresse, gid):
        b = self.boite(adresse)
        b['messages'].pop(gid, None)
        b['history_id'] += 1
        b['histoire'].append({'id': str(b['history_id']), 'messagesDeleted': [{'message': {'id': gid}}]})

    def etiqueter(self, adresse, gid, ajout=(), retrait=()):
        b = self.boite(adresse)
        m = b['messages'][gid]
        m['labelIds'] = sorted((set(m['labelIds']) | set(ajout)) - set(retrait))
        b['history_id'] += 1
        rec = {'id': str(b['history_id'])}
        if ajout:
            rec['labelsAdded'] = [{'message': {'id': gid}, 'labelIds': list(ajout)}]
        if retrait:
            rec['labelsRemoved'] = [{'message': {'id': gid}, 'labelIds': list(retrait)}]
        b['histoire'].append(rec)

    def panne(self, methode, contient, statut=None, entetes=None, apres=False, fois=1, corps=b'{}'):
        """Prochain appel qui correspond : statut imposé, ou ``Stop('delai_http_depasse')`` (statut None) ; ``apres`` : l'effet a lieu, la réponse se perd."""
        self.pannes.append({'methode': methode, 'contient': contient, 'statut': statut, 'entetes': entetes or {}, 'apres': apres, 'fois': fois, 'corps': corps})

    def ecritures_gmail(self):
        return [a for a in self.appels if a[1].startswith('https://gmail.googleapis.com') and a[0] != 'GET']

    def gmail(self):
        return [a for a in self.appels if a[1].startswith('https://gmail.googleapis.com')]

    # -------------------------------------------------------------------------------- transport
    def __call__(self, methode, url, entetes, corps):
        self.appels.append((methode, url))
        for p in list(self.pannes):
            if p['methode'] == methode and p['contient'] in url:
                p['fois'] -= 1
                if p['fois'] <= 0:
                    self.pannes.remove(p)
                if p['apres']:
                    self._traiter(methode, url, entetes, corps)
                if p['statut'] is None:
                    raise Stop('delai_http_depasse')
                return p['statut'], p['entetes'], p['corps']
        return self._traiter(methode, url, entetes, corps)

    @staticmethod
    def _json(valeur, statut=200):
        return statut, {}, json.dumps(valeur).encode()

    def _traiter(self, methode, url, entetes, corps):
        u = urlsplit(url)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        if url.startswith('https://oauth2.googleapis.com/token'):
            return self._jeton({k: v[0] for k, v in parse_qs(corps.decode()).items()})
        if url.startswith('https://oauth2.googleapis.com/revoke'):
            jeton = parse_qs(corps.decode())['token'][0]
            self.revocations.append(jeton)
            self.renouvellements.pop(jeton, None)
            return self._json({})
        jeton = (entetes.get('Authorization') or '')[len('Bearer '):]
        acces = self.acces.get(jeton)
        if not acces:
            return self._json({'error': {'code': 401, 'status': 'UNAUTHENTICATED'}}, 401)
        b, portees = self.boite(acces['adresse']), set(acces['portees'])
        chemin = u.path[len('/gmail/v1/users/me'):]
        brouillon = chemin.startswith('/drafts')
        if (brouillon and BROUILLONS not in portees) or (not brouillon and LECTURE not in portees):
            return self._json({'error': {'code': 403, 'status': 'PERMISSION_DENIED'}}, 403)
        if chemin == '/profile':
            return self._json({'emailAddress': acces['adresse'], 'messagesTotal': len(b['messages']), 'historyId': str(b['history_id'])})
        if chemin == '/messages':
            if q.get('q', '').startswith('rfc822msgid:'):
                cherche = '<%s>' % q['q'].split()[0][len('rfc822msgid:'):]
                return self._json({'messages': [{'id': i, 'threadId': m['threadId']} for i, m in b['envoyes'].items() if m['_mid'] == cherche]})
            ids = sorted(b['messages'], key=lambda i: int(b['messages'][i]['internalDate']), reverse=True)
            debut = int(q.get('pageToken') or 0)
            fin = debut + int(q.get('maxResults') or 100)
            out = {'messages': [{'id': i, 'threadId': b['messages'][i]['threadId']} for i in ids[debut:fin]], 'resultSizeEstimate': len(ids)}
            if fin < len(ids):
                out['nextPageToken'] = str(fin)
            return self._json(out)
        if chemin.startswith('/messages/') and '/attachments/' in chemin:
            aid = chemin.rsplit('/', 1)[1]
            donnees = b.get('pieces', {}).get(aid)
            return self._json({'size': len(donnees), 'data': b64(donnees)}) if donnees is not None else self._json({}, 404)
        if chemin.startswith('/messages/'):
            m = b['messages'].get(chemin.rsplit('/', 1)[1])
            return self._json({k: v for k, v in m.items() if not k.startswith('_')}) if m else self._json({'error': {'code': 404}}, 404)
        if chemin == '/history':
            if self.histoire_404:
                return self._json({'error': {'code': 404}}, 404)
            recs = [h for h in b['histoire'] if int(h['id']) > int(q['startHistoryId'])]
            debut = int(q.get('pageToken') or 0)
            out = {'history': recs[debut:debut + 2], 'historyId': str(b['history_id'])}
            if debut + 2 < len(recs):
                out['nextPageToken'] = str(debut + 2)
            return self._json(out)
        if chemin == '/drafts' and methode == 'POST':
            data = json.loads(corps)
            self.n += 1
            ident = 'r-%d' % self.n
            b['brouillons'][ident] = {'id': ident, 'message': {'id': 'md-%d' % self.n, 'threadId': data['message'].get('threadId'), 'raw': data['message']['raw']}}
            return self._json({'id': ident, 'message': {'id': 'md-%d' % self.n, 'threadId': data['message'].get('threadId'), 'labelIds': ['DRAFT']}})
        if chemin == '/drafts' and methode == 'GET':
            cherche = '<%s>' % q['q'][len('rfc822msgid:'):]
            trouves = [d for d in b['brouillons'].values() if self.entete(d['message']['raw'], 'Message-ID') == cherche]
            return self._json({'drafts': [{'id': d['id'], 'message': {'id': d['message']['id']}} for d in trouves]})
        if chemin == '/drafts/send':
            ident = json.loads(corps)['id']
            d = b['brouillons'].pop(ident, None)
            if not d:
                return self._json({'error': {'code': 404}}, 404)
            self.n += 1
            sid = 'sent-%d' % self.n
            b['envoyes'][sid] = {'raw': d['message']['raw'], 'threadId': d['message']['threadId'], '_mid': self.entete(d['message']['raw'], 'Message-ID')}
            return self._json({'id': sid, 'threadId': d['message']['threadId'], 'labelIds': ['SENT']})
        if chemin.startswith('/drafts/'):
            ident = chemin.rsplit('/', 1)[1]
            d = b['brouillons'].get(ident)
            if not d:
                return self._json({'error': {'code': 404}}, 404)
            if methode == 'PUT':
                d['message']['raw'] = json.loads(corps)['message']['raw']
                return self._json({'id': ident, 'message': {'id': d['message']['id']}})
            return self._json({'id': ident, 'message': dict(d['message'])})
        return self._json({'error': 'inconnu'}, 400)

    @staticmethod
    def entete(raw, nom):
        return str(BytesParser(policy=policy.default).parsebytes(deb64(raw)).get(nom, '')).strip()

    def _jeton(self, f):
        if f.get('client_id') != CLIENT_ID or f.get('client_secret') != SECRET:
            return self._json({'error': 'invalid_client'}, 401)
        if f['grant_type'] == 'authorization_code':
            c = self.codes.pop(f.get('code'), None)
            defi = base64.urlsafe_b64encode(hashlib.sha256(f.get('code_verifier', '').encode()).digest()).decode().rstrip('=')
            if not c or c['methode'] != 'S256' or c['defi'] != defi or c['redirect'] != f.get('redirect_uri'):
                return self._json({'error': 'invalid_grant'}, 400)
            self.n += 1
            acces, renouvellement = 'acc-%d' % self.n, 'ref-%d' % self.n
            self.acces[acces] = {'adresse': c['adresse'], 'portees': c['portees']}
            self.renouvellements[renouvellement] = {'adresse': c['adresse'], 'portees': c['portees']}
            self.comptes[renouvellement] = c['adresse']
            return self._json({'access_token': acces, 'refresh_token': renouvellement, 'expires_in': 3599, 'token_type': 'Bearer', 'scope': ' '.join(c['portees'])})
        if f['grant_type'] == 'refresh_token':
            r = self.renouvellements.get(f.get('refresh_token'))
            if not r:
                return self._json({'error': 'invalid_grant'}, 400)
            self.n += 1
            acces = 'acc-%d' % self.n
            self.acces[acces] = dict(r)
            if self.pendant_renouvellement:                                 # la réponse est prête quand la déconnexion survient
                self.pendant_renouvellement()
            return self._json({'access_token': acces, 'expires_in': 3599, 'token_type': 'Bearer', 'scope': ' '.join(r['portees'])})
        return self._json({'error': 'unsupported_grant_type'}, 400)


class Base(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.EngineTests(methodName='test_observation_has_no_mail_write')
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        from agent.vault567 import write
        secret = self.f.base / 'secrets' / 'google_oauth.secret'
        write(secret, SECRET)
        self.f.c['google5697'] = {'client_id': CLIENT_ID, 'client_secret_file': str(secret), 'redirect_uri': RETOUR}
        self.desk = Desk(self.f.c)
        self.addCleanup(self.desk.db.close)
        self.google = FauxGoogle()
        self.horloge = [time.time()]
        bloque = AssertionError('appel réseau non simulé')
        for p in (patch.object(gg, 'TRANSPORT', self.google), patch.object(gg, 'HORLOGE', lambda: self.horloge[0]),
                  patch('agent.common.HTTP.request', side_effect=bloque)):
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(membres5650.clear)

    def session(self, owner=CLAIRE):
        return gg.session_de({'axiorhub.user': {'session': 'session-' + owner}})

    def activer(self):
        gm.regler(self.desk, 'administrateur', True)

    def connecter(self, owner=CLAIRE, adresse=GMAIL_CLAIRE, brouillons=True, accordees=None):
        self.activer()
        depart = gg.demarrer(self.desk, owner, 'gmail', self.session(owner), ['gmail.lecture', 'gmail.brouillons'] if brouillons else None)
        return gg.terminer(self.desk, owner, self.session(owner), self.google.consentir(depart['url'], adresse, accordees))

    def conn(self, owner=CLAIRE):
        return gm.connexion_de(self.desk, owner)

    def synchro(self, owner=CLAIRE):
        return gm.synchroniser(self.desk, self.conn(owner)['id'])

    def lignes(self, owner=CLAIRE, filtre='tous'):
        from agent import courriels5696 as cx
        return [r for r in cx.lignes(self.desk, owner, frozenset(), filtre) if r.get('fournisseur') == 'gmail']

    def projet_pret(self, owner=CLAIRE, adresse=GMAIL_CLAIRE, cc=''):
        """Message de Camille MARTIN relevé, projet local préparé et déposé ; rend (projet, vue)."""
        self.google.message(adresse, 'Camille MARTIN <camille.martin@example.test>', 'Bordereau KERVILAC', 'Voici le bordereau.', cc=cc)
        self.synchro(owner)
        ligne = self.lignes(owner)[0]
        p = gm.preparer(self.desk, owner, ligne['mail_key'], texte='Bien reçu, merci.')['projet']
        v = gm.vue_projet(self.desk, owner, p)
        out = gm.deposer(self.desk, owner, 'avocat', p, v['version'], uuid.uuid4().hex)
        self.assertEqual(out['resultat'], 'depose', out)
        return p, gm.vue_projet(self.desk, owner, p)

    def envoyer(self, p, v, cle=None, owner=CLAIRE, role='avocat', relu=True):
        return gm.envoyer(self.desk, owner, role, p, v['version'], v['empreinte_depot'], cle or uuid.uuid4().hex, relu)


# ------------------------------------------------------------------------------------------------ C05 : OAuth Google sans identifiant à saisir
class C05OAuth(Base):
    def test_C05_configuration_du_serveur_jamais_du_code_ni_de_l_en_tete_host(self):
        source = (Path(gg.__file__).read_text(encoding='utf-8') + Path(gm.__file__).read_text(encoding='utf-8'))
        self.assertNotIn('apps.googleusercontent.com', source)            # aucun identifiant client dans le code
        self.assertNotIn('GOCSPX', source)                                # ni secret
        del self.desk.c['google5697']
        with self.assertRaises(Stop) as ctx:
            gg.demarrer(self.desk, CLAIRE, 'gmail', self.session())
        self.assertEqual(str(ctx.exception), 'google_non_configure')
        for mauvaise in ('http://pilote.example.test/p/api440/m5689/oauth/google/callback', RETOUR + '?suite=x', 'https://pilote.example.test/autre/retour'):
            self.desk.c['google5697'] = {'client_id': CLIENT_ID, 'client_secret_file': 'x', 'redirect_uri': mauvaise}
            with self.assertRaises(Stop) as ctx:
                gg.demarrer(self.desk, CLAIRE, 'gmail', self.session())
            self.assertEqual(str(ctx.exception), 'google_uri_retour_invalide')
        self.desk.c['google5697'] = {**self.f.c['google5697'], 'redirect_uri': RETOUR}
        # la route de départ ne lit jamais l'en-tête Host : l'URI vient de la configuration
        from agent import gmail_web5697
        self.activer()
        corps = b'{}'
        env = {'HTTP_X_AXIORHUB_USER': CLAIRE, 'HTTP_X_AXIORHUB_ROLE': 'avocat', 'HTTP_ORIGIN': ORIGINE, 'HTTP_X_CSRF_TOKEN': JETON, 'CONTENT_TYPE': 'application/json',
               'CONTENT_LENGTH': '2', 'wsgi.input': io.BytesIO(corps), 'HTTP_HOST': 'piege.example.test', 'axiorhub.user': {'session': 'session-' + CLAIRE}}
        out = gmail_web5697.api(env, self.desk, {'origin': ORIGINE, 'csrf': JETON}, '/p', 'm5689/gmail/connecter', {}, 'POST')
        url = json.loads(out['body'])['oauth_url']
        q = {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}
        self.assertEqual(q['redirect_uri'], RETOUR)
        self.assertNotIn('piege', url)
        self.assertEqual((q['client_id'], q['access_type'], q['code_challenge_method'], q['response_type']), (CLIENT_ID, 'offline', 'S256', 'code'))
        self.assertEqual(q['scope'], LECTURE)                              # lecture seule à la première connexion

    def test_C05_pkce_s256_et_state_a_usage_unique_lie_a_la_session(self):
        self.activer()
        depart = gg.demarrer(self.desk, CLAIRE, 'gmail', self.session())
        retour = self.google.consentir(depart['url'])
        stocke = self.desk.db.execute('SELECT etat_hash, verificateur FROM google5697_etats').fetchone()
        self.assertNotEqual(stocke['etat_hash'], retour['state'])          # l'état n'est gardé que haché
        for owner, session in ((CAMILLE, self.session(CAMILLE)), (CLAIRE, self.session(CAMILLE))):   # autre personne, autre session
            depart2 = gg.demarrer(self.desk, CLAIRE, 'gmail', self.session())
            r2 = self.google.consentir(depart2['url'])
            with self.assertRaises(Stop) as ctx:
                gg.terminer(self.desk, owner, session, r2)
            self.assertEqual(str(ctx.exception), 'google_etat_invalide')
            with self.assertRaises(Stop):                                   # consommé : la bonne session ne peut plus s'en servir
                gg.terminer(self.desk, CLAIRE, self.session(), r2)
        out = gg.terminer(self.desk, CLAIRE, self.session(), retour)
        self.assertEqual((out['connexion']['etat'], out['connexion']['adresse']), ('active', GMAIL_CLAIRE))
        self.assertNotIn('code', out['retour'])
        with self.assertRaises(Stop) as ctx:                                # rejeu du même retour
            gg.terminer(self.desk, CLAIRE, self.session(), retour)
        self.assertEqual(str(ctx.exception), 'google_etat_invalide')
        depart3 = gg.demarrer(self.desk, CLAIRE, 'gmail', self.session(), ['gmail.lecture', 'gmail.brouillons'])
        r3 = self.google.consentir(depart3['url'])
        self.horloge[0] += 601                                              # plus de 10 minutes
        with self.assertRaises(Stop) as ctx:
            gg.terminer(self.desk, CLAIRE, self.session(), r3)
        self.assertEqual(str(ctx.exception), 'google_etat_expire')
        self.assertEqual(gg.capacites(self.conn()), ['gmail.lecture'])    # rien n'a été ajouté par un retour expiré

    def test_C05_refus_chez_google_aucune_connexion_active_ni_jeton(self):
        self.activer()
        depart = gg.demarrer(self.desk, CLAIRE, 'gmail', self.session())
        q = {k: v[0] for k, v in parse_qs(urlsplit(depart['url']).query).items()}
        with self.assertRaises(Stop) as ctx:
            gg.terminer(self.desk, CLAIRE, self.session(), {'state': q['state'], 'error': 'access_denied'})
        self.assertEqual(str(ctx.exception), 'google_autorisation_refusee')
        self.assertNotEqual(self.conn()['etat'], 'active')
        self.assertFalse(gg._vault(self.desk, self.conn()['id'], 'access').exists())
        self.assertFalse([a for a in self.google.appels if 'token' in a[1]])   # aucun échange de code

    def test_C05_retour_redirige_sans_code_dans_l_url_finale(self):
        from agent import google_web5697
        self.activer()
        depart = gg.demarrer(self.desk, CLAIRE, 'gmail', self.session())
        retour = self.google.consentir(depart['url'])
        env = {'HTTP_X_AXIORHUB_USER': CLAIRE, 'HTTP_X_AXIORHUB_ROLE': 'avocat', 'axiorhub.user': {'session': 'session-' + CLAIRE}}
        out = google_web5697.api(env, self.desk, {'csrf': JETON}, '/p', 'm5689/oauth/google/callback', retour, 'GET')
        entetes = dict(out['headers'])
        self.assertEqual(out['status'][:3], '303')
        self.assertTrue(entetes['Location'].startswith('/p/parametres?rubrique=connexions&google=connecte'))
        self.assertNotIn(retour['code'], entetes['Location'])
        self.assertNotIn(retour['state'], entetes['Location'])
        self.assertEqual(entetes['Referrer-Policy'], 'no-referrer')
        out = google_web5697.api(env, self.desk, {'csrf': JETON}, '/p', 'm5689/oauth/google/callback', retour, 'GET')   # rejeu
        self.assertIn('google=google_etat_invalide', dict(out['headers'])['Location'])

    def test_C05_jetons_chiffres_separement_pour_chaque_connexion(self):
        self.connecter()
        self.connecter(CAMILLE, GMAIL_CAMILLE)
        a, b = self.conn(CLAIRE)['id'], self.conn(CAMILLE)['id']
        for ident in (a, b):
            brut = gg._vault(self.desk, ident, 'refresh').read_text()
            self.assertTrue(brut.startswith('AXIORHUB-VAULT-V1:'))
            self.assertNotIn('ref-', brut)
        cle_a = (gg._vault(self.desk, a, 'refresh').parent / '.master.key').read_bytes()
        cle_b = (gg._vault(self.desk, b, 'refresh').parent / '.master.key').read_bytes()
        self.assertNotEqual(cle_a, cle_b)                                   # une clé de chiffrement par connexion
        vue = json.dumps(gg.vue(self.conn()))
        self.assertNotIn('acc-', vue)
        self.assertNotIn('ref-', vue)

    def test_C05_invalid_grant_arret_et_reconnecter_sans_relance(self):
        self.connecter()
        ident = self.conn()['id']
        self.google.renouvellements.clear()                                 # autorisation retirée chez Google
        self.horloge[0] += 4000                                             # jeton d'accès expiré
        with self.assertRaises(Stop) as ctx:
            gg.appeler(self.desk, ident, 'GET', API + '/profile', capacite='gmail.lecture')
        self.assertEqual(str(ctx.exception), 'google_reconnexion_requise')
        self.assertEqual(self.conn()['etat'], 'a_reconnecter')
        avant = len(self.google.appels)
        for _ in range(3):
            with self.assertRaises(Stop) as ctx:
                gg.appeler(self.desk, ident, 'GET', API + '/profile')
            self.assertEqual(str(ctx.exception), 'google_reconnexion_requise')   # « Reconnecter », jamais « non connecté »
            self.assertEqual(gm.synchroniser(self.desk, ident)['etat'], 'arrete')
        self.assertEqual(len(self.google.appels), avant)                   # aucune boucle sur une autorisation retirée
        from agent import gmail_web5697
        self.assertIn('Reconnecter Gmail', gmail_web5697.section_html(self.desk, '/p', CLAIRE, 'avocat'))

    def test_C05_renouvellement_garde_le_jeton_de_renouvellement_et_se_serialise(self):
        self.connecter()
        ident = self.conn()['id']
        avant = gg._lire_jeton(self.desk, ident, 'refresh')
        self.horloge[0] += 4000
        gg.appeler(self.desk, ident, 'GET', API + '/profile')
        self.assertEqual(gg._lire_jeton(self.desk, ident, 'refresh'), avant)   # Google n'en renvoie pas : l'ancien est conservé
        # un autre processus tient le bail du renouvellement : on attend son résultat, sans second appel au point de jeton
        self.horloge[0] += 4000
        self.desk.db.execute('UPDATE google5697_connexions SET bail=? WHERE id=?', (self.horloge[0] + 60, ident))
        self.desk.db.commit()
        appels = len([a for a in self.google.appels if 'token' in a[1]])

        def autre_processus(_secondes):
            gg.jeton  # noqa: B018
            from agent.vault567 import write
            write(gg._vault(self.desk, ident, 'access'), 'acc-autre-processus')
            self.google.acces['acc-autre-processus'] = {'adresse': GMAIL_CLAIRE, 'portees': [LECTURE, BROUILLONS]}
            self.desk.db.execute('UPDATE google5697_connexions SET jeton_version=jeton_version+1, expire=?, bail=0 WHERE id=?', (self.horloge[0] + 3000, ident))
            self.desk.db.commit()
        with patch('agent.google5697.time.sleep', autre_processus):
            self.assertEqual(gg.jeton(self.desk, ident), 'acc-autre-processus')
        self.assertEqual(len([a for a in self.google.appels if 'token' in a[1]]), appels)

    def test_C05_deconnexion_pendant_un_renouvellement_l_emporte(self):
        self.connecter()
        ident = self.conn()['id']
        self.horloge[0] += 4000
        self.google.pendant_renouvellement = lambda: (setattr(self.google, 'pendant_renouvellement', None), gm.deconnecter(self.desk, CLAIRE))
        with self.assertRaises(Stop) as ctx:
            gg.jeton(self.desk, ident)
        self.assertEqual(str(ctx.exception), 'google_non_connecte')
        self.assertEqual(self.conn()['etat'], 'deconnectee')
        self.assertFalse(gg._vault(self.desk, ident, 'access').exists())   # le jeton obtenu pendant la déconnexion est jeté
        self.assertFalse(gg._vault(self.desk, ident, 'refresh').exists())

    def test_C05_deconnecter_revoque_et_efface_meme_si_la_revocation_echoue(self):
        self.connecter()
        ident = self.conn()['id']
        renouvellement = gg._lire_jeton(self.desk, ident, 'refresh')
        out = gm.deconnecter(self.desk, CLAIRE)
        self.assertEqual(out['revocation'], 'revoquee')
        self.assertEqual(self.google.revocations, [renouvellement])
        self.assertNotIn(renouvellement, [a[1] for a in self.google.appels])   # jamais dans une URL
        self.assertFalse(gg._vault(self.desk, ident, 'refresh').exists())
        self.connecter()
        self.google.panne('POST', '/revoke', 503)
        out = gm.deconnecter(self.desk, CLAIRE)
        self.assertEqual(out['revocation'], 'echec')
        self.assertFalse(gg._vault(self.desk, ident, 'refresh').exists())
        self.assertEqual(self.conn()['etat'], 'deconnectee')


# ------------------------------------------------------------------------------------------------ C06 : portées par capacité
class C06Portees(Base):
    def test_C06_lecture_puis_brouillons_jamais_modify_ni_send(self):
        self.activer()
        for voulues, attendu in ((None, {LECTURE}), (['gmail.lecture', 'gmail.brouillons'], {LECTURE, BROUILLONS})):
            url = gg.demarrer(self.desk, CLAIRE, 'gmail', self.session(), voulues)['url']
            portees = set(parse_qs(urlsplit(url).query)['scope'][0].split())
            self.assertEqual(portees, attendu)
            self.assertFalse(portees & gg.INTERDITES)
        with patch.dict(gg.PORTEES, {'gmail.brouillons': 'https://www.googleapis.com/auth/gmail.modify'}):   # mutation : le garde-fou refuse
            with self.assertRaises(Stop) as ctx:
                gg.demarrer(self.desk, CLAIRE, 'gmail', self.session(), ['gmail.brouillons'])
            self.assertEqual(str(ctx.exception), 'google_portee_interdite')

    def test_C06_consentement_partiel_capacites_reellement_disponibles(self):
        self.connecter(accordees=[LECTURE])                                 # brouillons décochés chez Google
        self.assertEqual(gg.capacites(self.conn()), ['gmail.lecture'])
        from agent import gmail_web5697
        html = gmail_web5697.section_html(self.desk, '/p', CLAIRE, 'avocat')
        self.assertIn('Autoriser les brouillons Gmail', html)
        self.assertIn(gg.LIBELLES['gmail.lecture'], html)
        self.assertNotIn(gg.LIBELLES['gmail.brouillons'], html)
        self.google.message(GMAIL_CLAIRE, 'Camille MARTIN <camille.martin@example.test>', 'Pièces')
        self.synchro()
        p = gm.preparer(self.desk, CLAIRE, self.lignes()[0]['mail_key'])['projet']
        with self.assertRaises(Stop) as ctx:
            gm.deposer(self.desk, CLAIRE, 'avocat', p, 1, uuid.uuid4().hex)
        self.assertEqual(str(ctx.exception), 'consentement_brouillons_requis')
        self.assertEqual(self.google.ecritures_gmail(), [])

    def test_C06_consentement_additionnel_sur_le_meme_compte_seulement(self):
        self.connecter(brouillons=False)
        url = gg.demarrer(self.desk, CLAIRE, 'gmail', self.session(), ['gmail.lecture', 'gmail.brouillons'])['url']
        self.assertEqual(parse_qs(urlsplit(url).query)['login_hint'][0], GMAIL_CLAIRE)
        with self.assertRaises(Stop) as ctx:
            gg.terminer(self.desk, CLAIRE, self.session(), self.google.consentir(url, adresse='autre.compte@example.test'))
        self.assertEqual(str(ctx.exception), 'google_compte_different')
        self.assertEqual(gg.capacites(self.conn()), ['gmail.lecture'])
        url = gg.demarrer(self.desk, CLAIRE, 'gmail', self.session(), ['gmail.lecture', 'gmail.brouillons'])['url']
        gg.terminer(self.desk, CLAIRE, self.session(), self.google.consentir(url))
        self.assertEqual(gg.capacites(self.conn()), ['gmail.lecture', 'gmail.brouillons'])


# ------------------------------------------------------------------------------------------------ C03 : fournisseur et synchronisation
class C03Synchro(Base):
    def test_C03_message_format_full_de_la_documentation_et_html_malveillant(self):
        self.connecter()
        msg = json.loads((FIXTURES / 'message_full.json').read_text(encoding='utf-8'))
        ligne = gm._normaliser(self.desk, self.conn(), msg)
        self.assertEqual((ligne['de_adresse'], ligne['objet'], ligne['message_id']), ('camille.martin@example.test', 'Bordereau KERVILAC', '<bordereau-kervilac@example.test>'))
        self.assertIn('RG 24/01234', ligne['texte'])
        self.assertEqual((ligne['sens'], ligne['motif']), ('recu', ''))
        self.assertEqual([p['name'] for p in json.loads(ligne['pieces_json'])], ['bordereau-KERVILAC.pdf'])
        self.assertEqual(ligne['thread_id'], '18c0a1b2c3d4e5f0')
        # même message sans partie texte : seul le HTML, lu sans script, sans image distante, le texte masqué signalé
        html = deb64(msg['payload']['parts'][0]['parts'][1]['body']['data']).decode()
        msg['payload']['parts'][0]['parts'] = [msg['payload']['parts'][0]['parts'][1]]
        ligne = gm._normaliser(self.desk, self.conn(), msg)
        self.assertIn('<script>', html)
        self.assertNotIn('alert(1)', ligne['texte'])
        self.assertNotIn('pixel.png', ligne['texte'])
        self.assertNotIn('envoie le dossier complet', ligne['texte'].split('[AxiorHub')[0])
        self.assertIn('[AxiorHub', ligne['texte'])

    def test_C03_historique_de_la_documentation_applique(self):
        self.connecter()
        row = self.conn()
        msg = json.loads((FIXTURES / 'message_full.json').read_text(encoding='utf-8'))
        gm._enregistrer(self.desk, gm._normaliser(self.desk, row, msg))
        hist = json.loads((FIXTURES / 'history_list.json').read_text(encoding='utf-8'))
        with patch.object(gg, 'appeler', side_effect=lambda d, c, m, u, *a, **k: hist if '/history' in u else (_ for _ in ()).throw(gg.ErreurGoogle('google_http_404', 404))):
            out = gm._incremental(self.desk, row, '4242')
        self.assertEqual(out['supprimes'], 1)
        labels = self.desk.db.execute('SELECT labels FROM gmail5697_messages WHERE gmail_id=?', (msg['id'],)).fetchone()[0]
        self.assertNotIn('UNREAD', labels.split())                         # lu dans Gmail : l'état lu est repris, rien n'est écrit chez Google
        self.assertEqual(self.desk.db.execute('SELECT history_id FROM gmail5697_synchro WHERE connexion_id=?', (row['id'],)).fetchone()[0], '4252')

    def test_C03_import_initial_borne_couverture_partielle(self):
        self.connecter()
        for n in range(260):
            self.google.message(GMAIL_CLAIRE, 'Tiers %d <tiers%d@example.test>' % (n, n), 'Message %d' % n)
        out = self.synchro()
        self.assertEqual((out['importes'], out['couverture']), (250, 'partielle'))
        n = self.desk.db.execute('SELECT COUNT(*) FROM gmail5697_messages').fetchone()[0]
        self.assertEqual(n, 250)
        s = gm.etat_synchro(self.desk, CLAIRE)
        self.assertEqual((s['couverture'], s['history_id']), ('partielle', str(self.google.boite(GMAIL_CLAIRE)['history_id'])))

    def test_C03_incremental_pagine_idempotent_sans_doublon(self):
        self.connecter()
        g1 = self.google.message(GMAIL_CLAIRE, 'Camille MARTIN <camille.martin@example.test>', 'Pièces MARTIN')[0]
        g2 = self.google.message(GMAIL_CLAIRE, 'Greffe <greffe@example.test>', 'Avis ZORVAL')[0]
        self.synchro()
        self.google.message(GMAIL_CLAIRE, 'Client <client@example.test>', 'Question ORLANDEC')
        self.google.message(GMAIL_CLAIRE, 'Confrère <confrere@example.test>', 'Bordereau KERVILAC')
        self.google.supprimer(GMAIL_CLAIRE, g2)
        self.google.etiqueter(GMAIL_CLAIRE, g1, retrait=('UNREAD',))
        out = self.synchro()
        self.assertEqual((out['ajoutes'], out['supprimes'], out['etiquettes']), (2, 1, 1))
        self.assertGreaterEqual(len([a for a in self.google.appels if '/history' in a[1]]), 2)   # pages suivies
        for _ in range(2):
            self.synchro()
        n = self.desk.db.execute('SELECT COUNT(*) FROM gmail5697_messages WHERE supprime=0').fetchone()[0]
        self.assertEqual(n, 3)
        self.assertEqual(len(self.lignes()), 3)
        self.assertEqual(self.google.ecritures_gmail(), [])                # lecture seule : zéro écriture Gmail

    def test_C03_404_sur_le_curseur_resynchronisation_bornee_signalee(self):
        self.connecter()
        self.google.message(GMAIL_CLAIRE, 'Camille MARTIN <camille.martin@example.test>', 'Pièces MARTIN')
        self.synchro()
        self.google.message(GMAIL_CLAIRE, 'Client <client@example.test>', 'Question ORLANDEC')
        self.google.histoire_404 = True
        out = self.synchro()
        self.assertTrue(out['resynchronisation'])
        s = gm.etat_synchro(self.desk, CLAIRE)
        self.assertIn('Historique Gmail expiré', s['fenetre_incomplete'])
        self.assertEqual(self.desk.db.execute('SELECT COUNT(*) FROM gmail5697_messages').fetchone()[0], 2)   # zéro doublon
        from agent import gmail_web5697
        self.assertIn('Historique Gmail expiré', gmail_web5697.section_html(self.desk, '/p', CLAIRE, 'avocat'))

    def test_C03_429_et_5xx_reprise_espacee_sans_boucle(self):
        self.connecter()
        self.google.panne('GET', '/profile', 429, {'Retry-After': '120'})
        out = self.synchro()
        self.assertEqual((out['etat'], out['dans']), ('espace', 120))
        avant = len(self.google.appels)
        self.assertEqual(self.synchro()['etat'], 'espace')                 # trop tôt : aucun appel
        self.assertEqual(len(self.google.appels), avant)
        self.horloge[0] += 121
        self.google.panne('GET', '/profile', 503)
        out = self.synchro()
        self.assertEqual(out['etat'], 'espace')
        self.assertEqual(out['dans'], 120)                                 # deuxième échec : attente croissante (60 puis 120)
        self.horloge[0] += 121
        self.assertEqual(self.synchro()['etat'], 'a_jour')

    def test_C03_un_seul_travail_actif_par_boite(self):
        self.connecter()
        gm._synchro(self.desk, self.conn()['id'])
        self.desk.db.execute('UPDATE gmail5697_synchro SET bail=?', (self.horloge[0] + 300,))
        self.desk.db.commit()
        avant = len(self.google.appels)
        self.assertEqual(self.synchro()['etat'], 'deja_en_cours')
        self.assertEqual(len(self.google.appels), avant)

    def test_C03_qualification_envoyes_copies_notifications_listes(self):
        self.connecter()
        self.google.message(GMAIL_CLAIRE, 'Camille MARTIN <camille.martin@example.test>', 'Demande MARTIN')
        self.google.message(GMAIL_CLAIRE, 'Claire DURAND <%s>' % GMAIL_CLAIRE, 'Réponse envoyée', etiquettes=('SENT',))
        self.google.message(GMAIL_CLAIRE, 'Cabinet <cabinet@example.test>', 'Copie du cabinet')
        self.google.message(GMAIL_CLAIRE, 'Lettre <info@example.test>', 'Lettre', entetes={'List-Unsubscribe': '<mailto:stop@example.test>'})
        self.google.message(GMAIL_CLAIRE, 'Boutique <promo@example.test>', 'Promotion', etiquettes=('INBOX', 'CATEGORY_PROMOTIONS'))
        self.google.message(GMAIL_CLAIRE, 'Robot <no-reply@example.test>', 'Avis automatique')
        self.google.message(GMAIL_CLAIRE, 'Banque <alerte@example.test>', 'Alerte', entetes={'Auto-Submitted': 'auto-generated'})
        self.synchro()
        self.assertEqual([r['objet'] for r in self.lignes()], ['Demande MARTIN'])
        with self.assertRaises(Stop) as ctx:
            cle = self.desk.db.execute("SELECT gmail_id FROM gmail5697_messages WHERE objet='Réponse envoyée'").fetchone()[0]
            gm.preparer(self.desk, CLAIRE, 'gmail:%s:%s' % (self.conn()['id'], cle))
        self.assertEqual(str(ctx.exception), 'message_sans_reponse')

    def test_C03_contrat_de_lecture_projection_locale_piece_a_la_demande(self):
        self.connecter()
        _, fil, _ = self.google.message(GMAIL_CLAIRE, 'Camille MARTIN <camille.martin@example.test>', 'Pièces MARTIN', piece=('note.txt', 'Note Valbrune'.encode()))
        self.google.message(GMAIL_CLAIRE, 'Camille MARTIN <camille.martin@example.test>', 'Re: Pièces MARTIN', fil=fil, jours=0)
        self.synchro()
        f = gm.FournisseurGmail(self.desk, CLAIRE)
        self.assertEqual(f.CAPACITES, ('lister', 'lire_message', 'lire_fil', 'lire_piece'))
        avant = len(self.google.appels)
        ligne = self.lignes()[0]
        ref = f.reference(ligne['mail_key'])
        self.assertEqual(len(f.lire_fil(ref)['messages']), 2)
        self.assertEqual(len(self.google.appels), avant)                   # fil et message : projection locale, aucun appel Google
        premier = self.desk.db.execute("SELECT gmail_id FROM gmail5697_messages WHERE objet='Pièces MARTIN'").fetchone()[0]
        piece = f.lire_piece(f.reference('gmail:%s:%s' % (self.conn()['id'], premier)), 0)
        self.assertIn('Valbrune', piece['text'])
        self.assertEqual(self.google.ecritures_gmail(), [])
        self.assertEqual(len(self.lignes()), 1)                            # un fil = une ligne (threadId), jamais par objet


# ------------------------------------------------------------------------------------------------ C04 : préparer, déposer, relire, envoyer
class C04Projet(Base):
    def test_C04_preparer_sans_ia_destinataire_expediteur_seulement(self):
        self.connecter()
        self.google.message(GMAIL_CLAIRE, 'Camille MARTIN <camille.martin@example.test>', 'Bordereau KERVILAC', cc='Bernard <bernard@example.test>')
        self.synchro()
        bloque = AssertionError('appel au modèle')
        self.desk.setting('missions567:pause', True)                       # IA en pause : la commande de l'avocat reste disponible
        with patch('agent.ai_gateway.estimate_and_check_budget', side_effect=bloque), patch.object(gg, 'TRANSPORT', side_effect=bloque):
            out = gm.preparer(self.desk, CLAIRE, self.lignes()[0]['mail_key'], texte='Bien reçu.')
            v = gm.vue_projet(self.desk, CLAIRE, out['projet'])
            gm.modifier(self.desk, CLAIRE, out['projet'], v['version'], {'texte': 'Bien reçu, merci.'})
            self.assertTrue(gm.preparer(self.desk, CLAIRE, self.lignes()[0]['mail_key'])['deja'])   # un seul projet par message
        v = gm.vue_projet(self.desk, CLAIRE, out['projet'])
        self.assertEqual(v['releve']['a'], ['camille.martin@example.test'])  # réponse à tous désactivée par défaut
        self.assertEqual(v['releve']['cc'], [])
        self.assertEqual(v['releve']['objet'], 'Re: Bordereau KERVILAC')
        self.assertEqual(v['etat'], 'local')
        self.assertEqual(self.lignes()[0]['statut'], 'reponse_prete')

    def test_C04_depot_dans_le_fil_avec_in_reply_to_et_references(self):
        self.connecter()
        _, fil, mid = self.google.message(GMAIL_CLAIRE, 'Camille MARTIN <camille.martin@example.test>', 'Bordereau KERVILAC')
        self.synchro()
        p = gm.preparer(self.desk, CLAIRE, self.lignes()[0]['mail_key'], texte='Bien reçu.')['projet']
        cle = uuid.uuid4().hex
        out = gm.deposer(self.desk, CLAIRE, 'avocat', p, 1, cle)
        self.assertEqual(out['resultat'], 'depose')
        self.assertTrue(gm.deposer(self.desk, CLAIRE, 'avocat', p, 1, cle)['deja'])   # double clic : un seul brouillon
        brouillons = list(self.google.boite(GMAIL_CLAIRE)['brouillons'].values())
        self.assertEqual(len(brouillons), 1)
        raw = brouillons[0]['message']['raw']
        self.assertEqual(brouillons[0]['message']['threadId'], fil)
        self.assertEqual(FauxGoogle.entete(raw, 'In-Reply-To'), mid)
        self.assertIn(mid, FauxGoogle.entete(raw, 'References'))
        self.assertEqual(FauxGoogle.entete(raw, 'From'), GMAIL_CLAIRE)
        self.assertFalse(self.google.boite(GMAIL_CLAIRE)['envoyes'])        # déposer n'est jamais envoyer

    def test_C04_modification_dans_gmail_jamais_ecrasee(self):
        self.connecter()
        p, v = self.projet_pret()
        d = next(iter(self.google.boite(GMAIL_CLAIRE)['brouillons'].values()))
        msg = BytesParser(policy=policy.default).parsebytes(deb64(d['message']['raw']))
        msg.set_content('Texte corrigé dans Gmail.')
        d['message']['raw'] = b64(msg.as_bytes())
        v = gm.modifier(self.desk, CLAIRE, p, v['version'], {'texte': 'Texte corrigé dans AxiorHub.'})
        puts = len([a for a in self.google.appels if a[0] == 'PUT'])
        with self.assertRaises(Stop) as ctx:
            gm.deposer(self.desk, CLAIRE, 'avocat', p, v['version'], uuid.uuid4().hex)
        self.assertEqual(str(ctx.exception), 'brouillon_modifie_a_distance')
        self.assertEqual(len([a for a in self.google.appels if a[0] == 'PUT']), puts)   # aucun écrasement
        v = gm.vue_projet(self.desk, CLAIRE, p)
        self.assertEqual(v['etat'], 'modifie_a_distance')
        v = gm.resoudre(self.desk, CLAIRE, p, v['version'], 'garder_gmail')
        self.assertEqual((v['etat'], v['texte']), ('depose', 'Texte corrigé dans Gmail.'))
        self.assertEqual(self.envoyer(p, v)['resultat'], 'envoye')

    def test_C04_relecture_exacte_puis_envoi_unique(self):
        self.connecter()
        p, v = self.projet_pret()
        r = v['releve']
        self.assertEqual((r['compte'], r['a'], r['cc'], r['objet'], r['texte'], r['pieces']),
                         (GMAIL_CLAIRE, ['camille.martin@example.test'], [], 'Re: Bordereau KERVILAC', 'Bien reçu, merci.', []))
        from agent import gmail_web5697
        html = gmail_web5697.releve_html(r)
        for attendu in (GMAIL_CLAIRE, 'camille.martin@example.test', 'Re: Bordereau KERVILAC', 'Bien reçu, merci.', 'Aucune pièce jointe'):
            self.assertIn(attendu, html)
        out = self.envoyer(p, v)
        self.assertEqual(out['resultat'], 'envoye')
        envoyes = list(self.google.boite(GMAIL_CLAIRE)['envoyes'].values())
        self.assertEqual(len(envoyes), 1)
        self.assertEqual(gm.empreinte(gm.releve(deb64(envoyes[0]['raw']))), v['empreinte_depot'])   # ce qui est parti = ce qui a été relu
        self.assertEqual(self.lignes()[0]['statut'], 'termine')

    def test_C04_double_clic_et_second_onglet_un_seul_message(self):
        self.connecter()
        p, v = self.projet_pret()
        cle = uuid.uuid4().hex
        self.assertEqual(self.envoyer(p, v, cle)['resultat'], 'envoye')
        self.assertTrue(self.envoyer(p, v, cle)['deja'])                   # même clic rejoué
        with self.assertRaises(Stop) as ctx:                                # autre onglet, autre clé, même version relue
            self.envoyer(p, v)
        self.assertEqual(str(ctx.exception), 'envoi_deja_demande')
        self.assertEqual(len(self.google.boite(GMAIL_CLAIRE)['envoyes']), 1)
        self.assertEqual(len([a for a in self.google.appels if a[1].endswith('/drafts/send')]), 1)

    def test_C04_projet_modifie_apres_la_relecture_n_envoie_rien(self):
        self.connecter()
        p, relu = self.projet_pret()
        gm.modifier(self.desk, CLAIRE, p, relu['version'], {'texte': 'Autre texte.'})
        with self.assertRaises(Stop) as ctx:
            self.envoyer(p, relu)                                           # version relue périmée
        self.assertEqual(str(ctx.exception), 'brouillon_a_deposer')
        v = gm.vue_projet(self.desk, CLAIRE, p)
        gm.deposer(self.desk, CLAIRE, 'avocat', p, v['version'], uuid.uuid4().hex)
        with self.assertRaises(Stop) as ctx:
            self.envoyer(p, relu)                                           # empreinte de l'ancienne relecture
        self.assertEqual(str(ctx.exception), 'version_perimee')
        d = next(iter(self.google.boite(GMAIL_CLAIRE)['brouillons'].values()))   # pièce ajoutée dans Gmail après la relecture
        msg = BytesParser(policy=policy.default).parsebytes(deb64(d['message']['raw']))
        msg.add_attachment(b'piece ajoutee', maintype='application', subtype='octet-stream', filename='ajout.bin')
        d['message']['raw'] = b64(msg.as_bytes())
        with self.assertRaises(Stop) as ctx:
            self.envoyer(p, gm.vue_projet(self.desk, CLAIRE, p))
        self.assertEqual(str(ctx.exception), 'brouillon_modifie_a_distance')
        self.assertFalse(self.google.boite(GMAIL_CLAIRE)['envoyes'])

    def test_C04_erreur_avant_envoi_rien_ne_part_nouveau_clic_possible(self):
        self.connecter()
        p, v = self.projet_pret()
        self.google.panne('POST', '/drafts/send', 400)
        with self.assertRaises(Stop):
            self.envoyer(p, v)
        v = gm.vue_projet(self.desk, CLAIRE, p)
        self.assertEqual(v['etat'], 'depose')
        self.assertFalse(self.google.boite(GMAIL_CLAIRE)['envoyes'])
        self.assertEqual(self.envoyer(p, v)['resultat'], 'envoye')
        self.assertEqual(len(self.google.boite(GMAIL_CLAIRE)['envoyes']), 1)

    def test_C04_reponse_perdue_apres_envoi_envoi_a_verifier_jamais_renvoye(self):
        self.connecter()
        p, v = self.projet_pret()
        self.google.panne('POST', '/drafts/send', None, apres=True)        # Google envoie, la réponse se perd
        out = self.envoyer(p, v)
        self.assertEqual((out['resultat'], out['etat']), ('a_verifier', 'envoi_a_verifier'))
        self.assertEqual(self.lignes()[0]['precision'], 'Envoi à vérifier')
        with self.assertRaises(Stop) as ctx:
            self.envoyer(p, gm.vue_projet(self.desk, CLAIRE, p))
        self.assertEqual(str(ctx.exception), 'envoi_deja_demande')
        gm.job(self.desk, {})                                               # automatismes : jamais de renvoi
        self.assertEqual(len([a for a in self.google.appels if a[1].endswith('/drafts/send')]), 1)
        out = gm.verifier(self.desk, CLAIRE, p)
        self.assertTrue(out['preuve'])
        self.assertEqual(out['etat'], 'envoye')
        self.assertEqual(len(self.google.boite(GMAIL_CLAIRE)['envoyes']), 1)

    def test_C04_depot_incertain_verifie_avant_tout_nouveau_depot(self):
        self.connecter()
        self.google.message(GMAIL_CLAIRE, 'Camille MARTIN <camille.martin@example.test>', 'Bordereau KERVILAC')
        self.synchro()
        p = gm.preparer(self.desk, CLAIRE, self.lignes()[0]['mail_key'], texte='Bien reçu.')['projet']
        self.google.panne('POST', '/drafts', None, apres=True)
        self.assertEqual(gm.deposer(self.desk, CLAIRE, 'avocat', p, 1, uuid.uuid4().hex)['resultat'], 'a_verifier')
        with self.assertRaises(Stop) as ctx:
            gm.deposer(self.desk, CLAIRE, 'avocat', p, gm.vue_projet(self.desk, CLAIRE, p)['version'], uuid.uuid4().hex)
        self.assertEqual(str(ctx.exception), 'depot_a_verifier')
        out = gm.verifier(self.desk, CLAIRE, p)
        self.assertEqual((out['preuve'], out['etat']), (True, 'depose'))
        self.assertEqual(len(self.google.boite(GMAIL_CLAIRE)['brouillons']), 1)   # zéro doublon

    def test_C04_l_agent_n_a_pas_la_capacite_d_envoi(self):
        from agent import connexions5652
        self.assertFalse([n for n in dir(gm.FournisseurGmail) if any(m in n for m in ('envoy', 'send', 'depos', 'draft', 'brouillon'))])
        self.assertFalse([t for t, (cap, _) in connexions5652.TOOLS.items() if cap.startswith('mail.') and cap not in ('mail.read', 'mail.create_draft')])
        self.assertNotIn('gmail', json.dumps(connexions5652.TOOLS))         # aucun outil de l'agent ne mène au fournisseur Gmail
        self.connecter()
        p, v = self.projet_pret()
        with self.assertRaises(Stop) as ctx:
            self.envoyer(p, v, relu=False)
        self.assertEqual(str(ctx.exception), 'relecture_requise')
        with self.assertRaises(Stop) as ctx:
            self.envoyer(p, v, role='assistant')
        self.assertEqual(str(ctx.exception), 'role_insuffisant')
        self.desk.active_job_id = 1                                         # dans un travail de l'agent
        with self.assertRaises(Stop) as ctx:
            self.envoyer(p, v)
        self.assertEqual(str(ctx.exception), 'geste_reserve_a_la_personne')
        self.assertFalse(self.google.boite(GMAIL_CLAIRE)['envoyes'])
        with patch.object(gm, '_exiger_personne', lambda desk: None):       # mutation : sans le garde-fou, le travail enverrait
            self.assertEqual(self.envoyer(p, v)['resultat'], 'envoye')
        self.desk.active_job_id = None

    def test_C04_routes_droits_et_csrf(self):
        from agent import gmail_web5697
        self.assertEqual(membres5650.category('POST', '/api440/m5689/gmail/envoyer'), 'envoyer')
        self.assertEqual(membres5650.category('POST', '/api440/m5689/gmail/deposer'), 'deposer')
        self.assertEqual(membres5650.category('POST', '/api440/m5689/gmail/reglage'), 'reglages')
        self.assertFalse(gmail_web5697.allowed('assistant', 'gmail/envoyer'))
        env = {'HTTP_X_AXIORHUB_USER': CLAIRE, 'HTTP_X_AXIORHUB_ROLE': 'avocat', 'HTTP_ORIGIN': 'https://piege.example.test', 'HTTP_X_CSRF_TOKEN': 'faux',
               'CONTENT_TYPE': 'application/json', 'CONTENT_LENGTH': '2', 'wsgi.input': io.BytesIO(b'{}')}
        out = gmail_web5697.api(env, self.desk, {'origin': ORIGINE, 'csrf': JETON}, '/p', 'm5689/gmail/envoyer', {}, 'POST')
        self.assertEqual(out['status'][:3], '403')


# ------------------------------------------------------------------------------------------------ C07 : non-régression, isolation, option
class C07NonRegression(Base):
    def test_C07_fournisseur_desactive_par_defaut(self):
        self.assertFalse(gm.actif(self.desk))
        from agent import gmail_web5697
        self.assertIn('désactivé', gmail_web5697.section_html(self.desk, '/p', CLAIRE, 'avocat'))
        env = {'HTTP_X_AXIORHUB_USER': CLAIRE, 'HTTP_X_AXIORHUB_ROLE': 'avocat', 'HTTP_ORIGIN': ORIGINE, 'HTTP_X_CSRF_TOKEN': JETON, 'CONTENT_TYPE': 'application/json',
               'CONTENT_LENGTH': '2', 'wsgi.input': io.BytesIO(b'{}')}
        out = gmail_web5697.api(env, self.desk, {'origin': ORIGINE, 'csrf': JETON}, '/p', 'm5689/gmail/connecter', {}, 'POST')
        self.assertEqual(json.loads(out['body'])['error'], 'gmail_desactive')
        with self.assertRaises(Stop):
            gm.regler(self.desk, 'avocat', True)                            # réservé à l'administrateur
        self.assertIsNone(gm.schedule(self.desk, time.time()))
        self.assertEqual(self.google.appels, [])

    def test_C07_desactiver_n_efface_rien_reactiver_retrouve_tout(self):
        self.connecter()
        p, v = self.projet_pret()
        gm.regler(self.desk, 'administrateur', False)
        self.assertEqual(self.lignes(), [])
        self.assertEqual(self.desk.db.execute('SELECT COUNT(*) FROM gmail5697_projets').fetchone()[0], 1)
        self.assertEqual(self.conn()['etat'], 'active')
        gm.regler(self.desk, 'administrateur', True)
        self.assertEqual(self.lignes()[0]['projet'], p)

    def test_C07_deux_comptes_aucun_contenu_ni_jeton_croise(self):
        self.connecter()
        self.connecter(CAMILLE, GMAIL_CAMILLE)
        self.google.message(GMAIL_CLAIRE, 'Client <client@example.test>', 'Question DURAND')
        self.google.message(GMAIL_CAMILLE, 'Greffe <greffe@example.test>', 'Avis ZORVAL')
        self.synchro(CLAIRE)
        self.synchro(CAMILLE)
        self.assertEqual([r['objet'] for r in self.lignes(CLAIRE)], ['Question DURAND'])
        self.assertEqual([r['objet'] for r in self.lignes(CAMILLE)], ['Avis ZORVAL'])
        cle_claire = self.lignes(CLAIRE)[0]['mail_key']
        with self.assertRaises(Stop) as ctx:
            gm.FournisseurGmail(self.desk, CAMILLE).reference(cle_claire)
        self.assertEqual(str(ctx.exception), 'message_absent')
        with self.assertRaises(Stop):
            gm.preparer(self.desk, CAMILLE, cle_claire)
        p = gm.preparer(self.desk, CLAIRE, cle_claire)['projet']
        with self.assertRaises(Stop) as ctx:
            gm.vue_projet(self.desk, CAMILLE, p)
        self.assertEqual(str(ctx.exception), 'projet_absent')
        # un échec Google pour Claire ne touche pas Camille
        self.google.renouvellements = {k: v for k, v in self.google.renouvellements.items() if v['adresse'] != GMAIL_CLAIRE}
        self.horloge[0] += 4000
        self.assertEqual(self.synchro(CLAIRE)['etat'], 'arrete')
        self.assertEqual(self.synchro(CAMILLE)['etat'], 'a_jour')
        jetons_camille = {a for a, x in self.google.acces.items() if x['adresse'] == GMAIL_CAMILLE}
        self.assertTrue(all(gg._lire_jeton(self.desk, self.conn(CLAIRE)['id'], 'access') not in jetons_camille for _ in (1,)))

    def test_C07_dossier_reserve_masque_la_ligne_gmail(self):
        self.connecter()
        self.google.message(GMAIL_CLAIRE, 'Client <client@example.test>', 'Question DURAND')
        self.synchro()
        p = gm.preparer(self.desk, CLAIRE, self.lignes()[0]['mail_key'], matter='DOS-001')['projet']
        self.assertTrue(p)
        from agent import courriels5696 as cx
        lignes = [r for r in cx.lignes(self.desk, CLAIRE, frozenset({'DOS-001'}), 'tous') if r.get('fournisseur') == 'gmail']
        self.assertEqual(lignes, [])

    def test_C07_premier_ecran_utilisable_si_google_ne_repond_pas_et_rendu_sans_appel(self):
        self.connecter()
        self.google.message(GMAIL_CLAIRE, 'Client <client@example.test>', 'Question DURAND')
        self.synchro()
        self.desk.setting('courriels5696:vue_assistee:' + CLAIRE, True)
        from agent import web440, courriels_web5696
        avant = len(self.google.appels)
        with patch.object(gg, 'TRANSPORT', side_effect=Stop('connexion_http_indisponible')):
            env = {'HTTP_X_AXIORHUB_USER': CLAIRE, 'HTTP_X_AXIORHUB_ROLE': 'avocat'}
            page = web440.drafts_page(self.desk, {'csrf': JETON, 'origin': ORIGINE}, '/p', {'vue': 'assistante'}, env)
            self.assertIn('Question DURAND', page)
            fil = self.lignes()[0]['fil']
            page = web440.drafts_page(self.desk, {'csrf': JETON, 'origin': ORIGINE}, '/p', {'vue': 'assistante', 'fil': fil}, env)
            self.assertIn('Préparer une réponse', page)
            out = courriels_web5696.api(env, self.desk, {'origin': ORIGINE, 'csrf': JETON}, '/p', 'm5689/courriels/fil', {'fil': fil}, 'GET')
            self.assertEqual(json.loads(out['body'])['messages'][0]['objet'], 'Question DURAND')
            from agent import parametres5621
            self.assertTrue(parametres5621)
            from agent import gmail_web5697
            self.assertIn(GMAIL_CLAIRE, gmail_web5697.section_html(self.desk, '/p', CLAIRE, 'avocat'))
        self.assertEqual(len(self.google.appels), avant)                   # aucun appel Google au rendu de la liste ni du détail

    def test_C07_route_preparer_et_decisions_de_suivi_sur_une_ligne_gmail(self):
        self.connecter()
        self.google.message(GMAIL_CLAIRE, 'Client <client@example.test>', 'Question DURAND')
        self.synchro()
        self.desk.setting('courriels5696:vue_assistee:' + CLAIRE, True)
        from agent import courriels_web5696
        ligne = self.lignes()[0]

        def appel(route, data):
            corps = json.dumps(data).encode()
            env = {'HTTP_X_AXIORHUB_USER': CLAIRE, 'HTTP_X_AXIORHUB_ROLE': 'avocat', 'HTTP_ORIGIN': ORIGINE, 'HTTP_X_CSRF_TOKEN': JETON, 'CONTENT_TYPE': 'application/json',
                   'CONTENT_LENGTH': str(len(corps)), 'wsgi.input': io.BytesIO(corps)}
            out = courriels_web5696.api(env, self.desk, {'origin': ORIGINE, 'csrf': JETON}, '/p', 'm5689/' + route, {}, 'POST')
            return out['status'][:3], json.loads(out['body'])
        code, out = appel('courriels/decision', {'fil': ligne['fil'], 'action': 'reporter', 'version': 0, 'idempotence': uuid.uuid4().hex,
                                                 'jusqua': (datetime.now() + timedelta(days=3)).strftime('%Y-%m-%dT09:00')})
        self.assertEqual(code, '200', out)
        self.assertEqual(self.lignes()[0]['statut'], 'reporte')
        code, out = appel('courriels/decision', {'fil': ligne['fil'], 'action': 'rouvrir', 'version': out['version'], 'idempotence': uuid.uuid4().hex})
        self.assertEqual(code, '200', out)
        code, out = appel('courriels/preparer', {'fil': ligne['fil'], 'version': out['version'], 'idempotence': uuid.uuid4().hex, 'texte': 'Bien reçu.'})
        self.assertEqual(code, '200', out)
        self.assertEqual(self.lignes()[0]['statut'], 'reponse_prete')
        self.assertEqual(self.google.ecritures_gmail(), [])

    def test_C07_export_et_relance_periodique_en_lecture_seule(self):
        from agent import export5688
        tables = {t[2] for t in export5688.TABLES}
        self.assertIn('gmail5697_projets', tables)
        self.assertNotIn('google5697_connexions', tables)                  # jamais une table de comptes ou de jetons
        self.connecter()
        ident = gm.schedule(self.desk, time.time())
        self.assertTrue(ident)
        self.assertIsNone(gm.schedule(self.desk, time.time()))             # pas avant 5 minutes
        self.google.message(GMAIL_CLAIRE, 'Client <client@example.test>', 'Question DURAND')
        self.assertEqual(list(gm.job(self.desk, {}).values())[0], {self.conn()['id'][:10]: 'a_jour'})
        self.assertEqual(self.google.ecritures_gmail(), [])


# ------------------------------------------------------------------------------------------------ B01 : garde par message (résolveur canonique)
class B01Gmail(Base):
    def rattacher(self, gid, matter='DOS-001'):
        """Rattachement confirmé d'un message Gmail à un dossier, au registre du serveur, par sa clé canonique (connexion, identifiant Gmail)."""
        from agent.messages5697 import cle_gmail
        self.desk.db.executescript('''CREATE TABLE IF NOT EXISTS portfolio_mail_links(id TEXT PRIMARY KEY, mail_key TEXT NOT NULL, message_id TEXT NOT NULL,
            thread_root TEXT NOT NULL, folder TEXT NOT NULL, uid TEXT NOT NULL, matter TEXT NOT NULL, confidence INTEGER NOT NULL, level TEXT NOT NULL,
            source TEXT NOT NULL, sender TEXT NOT NULL, recipients TEXT NOT NULL, mail_date TEXT NOT NULL, status TEXT NOT NULL, updated TEXT NOT NULL);''')
        self.desk.db.execute("INSERT INTO portfolio_mail_links VALUES(?,?,'','','Gmail','',?,100,'certain','avocat','','','','confirmed','')",
                             (uuid.uuid4().hex, cle_gmail(self.conn()['id'], gid), matter))
        self.desk.db.commit()

    def test_B01_cle_canonique_gmail_du_resolveur(self):
        from agent import messages5697 as M
        self.connecter()
        gid = self.google.message(GMAIL_CLAIRE, 'Client <client@example.test>', 'Question DURAND')[0]
        self.synchro()
        ident = self.conn()['id']
        a = M.resoudre(self.desk, 'gmail:%s:%s' % (ident, gid))
        b = M.resoudre(self.desk, {'connexion_id': ident, 'gmail_id': gid, 'matter': 'DOS-001'})   # un champ « matter » joint ne compte pas
        self.assertEqual((a.mail_key, b.mail_key), (M.cle_gmail(ident, gid),) * 2)
        self.assertEqual((a.compte, a.uid, a.dossiers), ('gmail:' + ident, gid, frozenset()))
        self.assertNotEqual(M.cle_gmail(ident, gid), M.cle_gmail('autre-connexion', gid))   # jamais de fusion entre comptes
        self.rattacher(gid)
        self.assertEqual(M.resoudre(self.desk, 'gmail:%s:%s' % (ident, gid)).dossiers, frozenset({'DOS-001'}))

    def test_B01_message_reserve_filtre_avant_regroupement_et_lecture(self):
        self.connecter()
        g1, fil, _ = self.google.message(GMAIL_CLAIRE, 'Client <client@example.test>', 'Question DURAND', jours=-1)
        g2 = self.google.message(GMAIL_CLAIRE, 'Client <client@example.test>', 'Re: Question DURAND', fil=fil)[0]
        self.synchro()
        self.rattacher(g2)
        from agent import courriels5696 as cx
        masque = frozenset({'DOS-001'})
        lignes = [r for r in cx.lignes(self.desk, CLAIRE, masque, 'tous') if r.get('fournisseur') == 'gmail']
        self.assertEqual([r['objet'] for r in lignes], ['Question DURAND'])      # le fil est représenté par le message visible
        f = gm.FournisseurGmail(self.desk, CLAIRE, masque)
        cle2 = 'gmail:%s:%s' % (self.conn()['id'], g2)
        with self.assertRaises(Stop) as ctx:
            f.reference(cle2)
        self.assertEqual(str(ctx.exception), 'dossier_reserve')
        ref1 = f.reference(lignes[0]['mail_key'])
        self.assertEqual([m['objet'] for m in f.lire_fil(ref1)['messages']], ['Question DURAND'])   # chaque message du fil contrôlé
        with self.assertRaises(Stop) as ctx:
            gm.preparer(self.desk, CLAIRE, cle2, hidden=masque)
        self.assertEqual(str(ctx.exception), 'dossier_reserve')
        with self.assertRaises(Stop):
            gm.preparer(self.desk, CLAIRE, lignes[0]['mail_key'], matter='DOS-001', hidden=masque)   # dossier choisi réservé
        self.assertEqual(self.desk.db.execute('SELECT COUNT(*) FROM gmail5697_projets').fetchone()[0], 0)
        self.assertEqual([r['objet'] for r in self.lignes()][0], 'Re: Question DURAND')               # sans masque : le plus récent

    def test_B01_routes_reponse_neutre(self):
        self.connecter()
        gid = self.google.message(GMAIL_CLAIRE, 'Client <client@example.test>', 'Question DURAND')[0]
        self.synchro()
        self.desk.setting('courriels5696:vue_assistee:' + CLAIRE, True)
        fil = self.lignes()[0]['fil']
        p = gm.preparer(self.desk, CLAIRE, self.lignes()[0]['mail_key'], matter='DOS-001')['projet']
        from agent import courriels_web5696, gmail_web5697
        env = {'HTTP_X_AXIORHUB_USER': CLAIRE, 'HTTP_X_AXIORHUB_ROLE': 'avocat'}
        with patch('agent.acces5697.masques', return_value=frozenset({'DOS-001'})):
            out = courriels_web5696.api(env, self.desk, {'origin': ORIGINE, 'csrf': JETON}, '/p', 'm5689/courriels/fil', {'fil': fil}, 'GET')
            self.assertEqual((out['status'][:3], json.loads(out['body'])['error']), ('404', 'introuvable'))
            corps = json.dumps({'projet': p, 'version': 1, 'idempotence': uuid.uuid4().hex}).encode()
            env2 = {**env, 'HTTP_ORIGIN': ORIGINE, 'HTTP_X_CSRF_TOKEN': JETON, 'CONTENT_TYPE': 'application/json', 'CONTENT_LENGTH': str(len(corps)), 'wsgi.input': io.BytesIO(corps)}
            out = gmail_web5697.api(env2, self.desk, {'origin': ORIGINE, 'csrf': JETON}, '/p', 'm5689/gmail/deposer', {}, 'POST')
            self.assertEqual((out['status'][:3], json.loads(out['body'])['error']), ('404', 'projet_absent'))
        self.assertEqual(self.google.ecritures_gmail(), [])
        self.assertTrue(gid)


# ------------------------------------------------------------------------------------------------ C04 et C06 : préparation par l'IA (faux modèle local)
SUR = {'grounded': True, 'recipient_safe': True, 'ignores_embedded_instructions': True, 'requires_lawyer': False, 'no_new_commitment': True,
       'answers_question': True, 'reasons': []}
PROJET_IA = {'body': 'Madame,\n\nJ’accuse réception du bordereau du dossier KERVILAC et reviens vers vous après examen.\n\nBien cordialement,',
             'source_ids': ['incoming'], 'limits': [], 'requires_decision': False}


class FauxModele:
    """Faux modèle de rédaction : note la configuration routée de chaque appel (fournisseur), rend un projet ; ``pendant`` simule un événement
    survenu pendant la génération (nouveau message, correction de l'avocat)."""

    def __init__(self, pendant=None):
        self.configs, self.charges, self.pendant = [], [], pendant
        modele = self

        class Fabrique:
            def __init__(self, cfg):
                modele.configs.append(dict(cfg))

            def ask(self, etape, charge):
                modele.charges.append((etape, json.loads(json.dumps(charge, ensure_ascii=False, default=str))))
                if etape == 'verify':
                    return dict(SUR)
                if modele.pendant:
                    action, modele.pendant = modele.pendant, None
                    action()
                return json.loads(json.dumps(PROJET_IA, ensure_ascii=False))
        self.Fabrique = Fabrique


EXTERNE = {'routeur5682': True, 'provider_type': 'routeur', 'provider_id': 'externe-fictif'}


class Index:
    def sources(self, *a, **k):
        return ([], {})


class C04PreparationIA(Base):
    def setUp(self):
        super().setUp()
        self.connecter()
        self.gid, self.fil, self.mid = self.google.message(GMAIL_CLAIRE, 'Camille MARTIN <camille.martin@example.test>', 'Bordereau KERVILAC',
                                                           'Bonjour Maître, voici le bordereau des pièces.')
        self.synchro()
        self.cle = 'gmail:%s:%s' % (self.conn()['id'], self.gid)

    def executer(self, modele, job):
        from agent import intelligence
        args = json.loads(self.desk.db.execute('SELECT args FROM jobs WHERE id=?', (job,)).fetchone()[0])
        with patch.object(intelligence, 'Model', modele.Fabrique), patch.object(intelligence, 'source_index', return_value=Index()), \
                patch.object(intelligence, 'DAV'), patch.object(intelligence, 'Mailbox', side_effect=AssertionError('connexion IMAP')), \
                patch('agent.politique5682.routed', return_value=EXTERNE), patch('agent.secours564.attach', side_effect=lambda b, c, p: {**b, 'secours': {'delay': 1}}):
            return gm.job_ia(self.desk, args)   # la politique du cabinet routerait vers un service distant : le contenu Gmail reste local

    def test_C04_ia_circuit_commun_modele_local_projet_local(self):
        out = gm.demander_ia(self.desk, CLAIRE, 'avocat', self.cle, 'DOS-001', 'Accuser réception.')
        self.assertFalse(out['deja'])
        self.assertTrue(gm.demander_ia(self.desk, CLAIRE, 'avocat', self.cle, 'DOS-001')['deja'])   # un seul travail par message et par génération
        self.assertEqual(self.desk.db.execute("SELECT COUNT(*) FROM jobs WHERE kind='gmail5697_redaction'").fetchone()[0], 1)
        self.assertEqual(self.lignes()[0]['statut'], 'en_preparation')
        modele = FauxModele()
        self.assertEqual(self.executer(modele, out['job'])['etat'], 'pret')
        self.assertTrue(modele.configs and all(c.get('provider_type') == 'ollama' and not c.get('secours') and not c.get('hybrid_external') for c in modele.configs))
        redaction = [c for e, c in modele.charges if e == 'manual_draft'][0]
        self.assertIn('voici le bordereau', redaction['incoming']['text'])            # copie locale Gmail, lue par le fournisseur
        self.assertEqual(redaction['dossier']['id'], 'DOS-001')
        self.assertEqual(redaction['reply_recipients'], ['camille.martin@example.test'])
        v = gm.vue_projet(self.desk, CLAIRE, out['projet'])
        self.assertEqual((v['etat'], v['ia_etat'], v['texte']), ('local', 'pret', PROJET_IA['body']))
        self.assertEqual(self.desk.db.execute('SELECT COUNT(*) FROM manual_drafts').fetchone()[0], 0)   # jamais un dépôt IMAP
        self.assertEqual(self.google.ecritures_gmail(), [])                                            # rien n'est déposé par l'agent
        self.assertEqual(self.lignes()[0]['statut'], 'reponse_prete')
        self.assertEqual(gm.deposer(self.desk, CLAIRE, 'avocat', out['projet'], v['version'], uuid.uuid4().hex)['resultat'], 'depose')   # l'avocat dépose

    def test_C04_ia_dossier_confirme_droits_relus_au_travail(self):
        with self.assertRaises(Stop) as ctx:
            gm.demander_ia(self.desk, CLAIRE, 'avocat', self.cle)
        self.assertEqual(str(ctx.exception), 'dossier_a_confirmer')
        with self.assertRaises(Stop) as ctx:
            gm.demander_ia(self.desk, CLAIRE, 'avocat', self.cle, 'DOS-001', hidden=frozenset({'DOS-001'}))
        self.assertEqual(str(ctx.exception), 'dossier_reserve')
        out = gm.demander_ia(self.desk, CLAIRE, 'avocat', self.cle, 'DOS-001')
        modele = FauxModele()
        with patch('agent.acces5697.masques', return_value=frozenset({'DOS-001'})):   # droits retirés entre la demande et le travail
            with self.assertRaises(Stop) as ctx:
                self.executer(modele, out['job'])
        self.assertEqual(str(ctx.exception), 'introuvable')
        self.assertEqual(modele.charges, [])                                           # rien n'a été lu ni envoyé au modèle

    def test_C04_ia_nouveau_message_pendant_la_generation_projet_perime(self):
        out = gm.demander_ia(self.desk, CLAIRE, 'avocat', self.cle, 'DOS-001')

        def arrivee():
            self.google.message(GMAIL_CLAIRE, 'Camille MARTIN <camille.martin@example.test>', 'Re: Bordereau KERVILAC', 'Pièce 7 en plus.', fil=self.fil)
            self.synchro()
        self.assertEqual(self.executer(FauxModele(arrivee), out['job'])['etat'], 'perime')
        v = gm.vue_projet(self.desk, CLAIRE, out['projet'])
        self.assertTrue(v['perime'])
        with self.assertRaises(Stop) as ctx:
            gm.deposer(self.desk, CLAIRE, 'avocat', out['projet'], v['version'], uuid.uuid4().hex)
        self.assertEqual(str(ctx.exception), 'projet_perime')
        self.assertEqual(self.google.ecritures_gmail(), [])
        v = gm.modifier(self.desk, CLAIRE, out['projet'], v['version'], {'texte': 'Bien reçu, y compris la pièce 7.'})
        self.assertFalse(v['perime'])
        self.assertEqual(gm.deposer(self.desk, CLAIRE, 'avocat', out['projet'], v['version'], uuid.uuid4().hex)['resultat'], 'depose')

    def test_C04_ia_projet_corrige_par_l_avocat_jamais_remplace(self):
        out = gm.demander_ia(self.desk, CLAIRE, 'avocat', self.cle, 'DOS-001')

        def correction():
            v = gm.vue_projet(self.desk, CLAIRE, out['projet'])
            gm.modifier(self.desk, CLAIRE, out['projet'], v['version'], {'texte': 'Ma version, écrite à la main.'})
        self.assertEqual(self.executer(FauxModele(correction), out['job'])['etat'], 'non_applique')
        v = gm.vue_projet(self.desk, CLAIRE, out['projet'])
        self.assertEqual((v['texte'], v['texte_ia']), ('Ma version, écrite à la main.', PROJET_IA['body']))
        out2 = gm.demander_ia(self.desk, CLAIRE, 'avocat', self.cle, 'DOS-001')        # nouvelle génération, même règle
        self.assertFalse(out2['deja'])
        self.assertEqual(self.executer(FauxModele(), out2['job'])['etat'], 'non_applique')
        self.assertEqual(gm.vue_projet(self.desk, CLAIRE, out['projet'])['texte'], 'Ma version, écrite à la main.')
        from agent import gmail_web5697
        ligne = self.lignes()[0]
        self.assertIn('Voir la proposition de l’IA', gmail_web5697.principal_html(self.desk, '/p', ligne, ''))

    def test_C04_ia_pause_et_generation_perimee_n_ecrivent_rien(self):
        out = gm.demander_ia(self.desk, CLAIRE, 'avocat', self.cle, 'DOS-001')
        self.desk.db.execute("UPDATE jobs SET status='cancel_requested', stop_reason='pause' WHERE id=?", (out['job'],))   # pause de l'IA demandée
        self.desk.db.commit()
        self.desk.active_job_id = out['job']                       # le travail relit son état réel avant tout effet
        modele = FauxModele()
        with self.assertRaises(Stop) as ctx:
            self.executer(modele, out['job'])
        self.assertEqual(str(ctx.exception), 'ia_en_pause')
        self.desk.active_job_id = None
        self.assertEqual(modele.charges, [])
        self.assertEqual(gm.vue_projet(self.desk, CLAIRE, out['projet'])['texte'], '')
        self.desk.db.execute("UPDATE jobs SET status='done' WHERE id=?", (out['job'],))
        self.desk.db.commit()
        out2 = gm.demander_ia(self.desk, CLAIRE, 'avocat', self.cle, 'DOS-001')
        args = json.loads(self.desk.db.execute('SELECT args FROM jobs WHERE id=?', (out['job'],)).fetchone()[0])
        self.assertEqual(gm.job_ia(self.desk, args)['etat'], 'remplace')   # l'ancienne génération ne remplace jamais la nouvelle
        self.assertNotEqual(out2['job'], out['job'])

    def test_C06_contenu_gmail_modele_local_sans_repli_distant(self):
        from agent import model
        externe = {'routeur5682': True, 'provider_type': 'routeur', 'provider_id': 'externe-fictif'}
        with patch('agent.politique5682.routed', return_value=externe), patch('agent.secours564.attach', side_effect=lambda b, c, p: {**b, 'secours': {'delay': 1}}):
            self.assertTrue(model.routed_config(self.desk.c, 'mail_drafting').get('routeur5682'))   # hors Gmail : la politique du cabinet route
            with model.modele_local_seul():
                cfg = model.routed_config(self.desk.c, 'mail_drafting')
            self.assertEqual(cfg['provider_type'], 'ollama')
            self.assertFalse(cfg.get('secours') or cfg.get('routeur5682') or cfg.get('hybrid_external'))
        self.desk.c['model_routing'] = {'mail_drafting': {'provider': 'externe-fictif'}}     # route historique vers un fournisseur distant
        with patch('agent.hybrid400.policy', return_value={'mode': 'manual', 'allowed_purposes': [], 'max_external_characters': 120000}):
            with self.assertRaises(Stop):
                model.routed_config(self.desk.c, 'mail_drafting')
            with model.modele_local_seul():
                self.assertEqual(model.routed_config(self.desk.c, 'mail_drafting')['provider_id'], 'ollama')
        del self.desk.c['model_routing']
        with patch('agent.membres5650.ai_denied', return_value={'courriels'}):          # « Ce que l'IA peut voir » : courriels fermés
            with self.assertRaises(Stop) as ctx:
                gm.demander_ia(self.desk, CLAIRE, 'avocat', self.cle, 'DOS-001')
        self.assertEqual(str(ctx.exception), 'categorie_fermee')
        self.assertEqual(self.desk.db.execute("SELECT COUNT(*) FROM jobs WHERE kind='gmail5697_redaction'").fetchone()[0], 0)

    def test_C04_ia_route_et_droit(self):
        from agent import gmail_web5697
        self.desk.setting('courriels5696:vue_assistee:' + CLAIRE, True)
        self.assertEqual(membres5650.category('POST', '/api440/m5689/gmail/preparer_ia'), 'deposer')
        corps = json.dumps({'fil': self.lignes()[0]['fil'], 'dossier': 'DOS-001', 'instruction': 'Accuser réception.'}).encode()
        env = {'HTTP_X_AXIORHUB_USER': CLAIRE, 'HTTP_X_AXIORHUB_ROLE': 'avocat', 'HTTP_ORIGIN': ORIGINE, 'HTTP_X_CSRF_TOKEN': JETON, 'CONTENT_TYPE': 'application/json',
               'CONTENT_LENGTH': str(len(corps)), 'wsgi.input': io.BytesIO(corps)}
        out = gmail_web5697.api(env, self.desk, {'origin': ORIGINE, 'csrf': JETON}, '/p', 'm5689/gmail/preparer_ia', {}, 'POST')
        self.assertEqual(out['status'][:3], '200', out['body'])
        self.assertTrue(json.loads(out['body'])['job'])
        html = gmail_web5697.principal_html(self.desk, '/p', self.lignes()[0], '')
        self.assertIn('Préparation par l’IA en cours', html)


class ReseauBloque(Base):
    def test_C05_tout_appel_reseau_non_simule_echoue(self):
        """Sans transport simulé, le client passe par ``common.HTTP`` : bloqué ici, l'appel fait échouer le test au lieu de sortir."""
        self.activer()
        depart = gg.demarrer(self.desk, CLAIRE, 'gmail', self.session())
        retour = self.google.consentir(depart['url'])
        with patch.object(gg, 'TRANSPORT', None):
            with self.assertRaises(AssertionError):
                gg.terminer(self.desk, CLAIRE, self.session(), retour)


if __name__ == '__main__':
    unittest.main()
