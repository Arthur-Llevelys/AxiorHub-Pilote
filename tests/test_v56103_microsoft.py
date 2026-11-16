"""5.6.103, lot M : connexion Microsoft simple par l'application publique intégrée (PKCE sur le poste, code d'appareil sur le serveur),
Outlook courrier (lecture, brouillons avec pièces jointes, jamais d'envoi), agenda Outlook sur l'application intégrée (l'application propre
du cabinet reste possible), source OneDrive / SharePoint pour la page « Fichiers ».

Faux Microsoft au niveau du transport (``microsoft56103.TRANSPORT``, ``outlook56103.ENVOI_TRANSPORT``, ``onedrive56103.TELECHARGEMENT``) :
point d'identité (code + PKCE S256, code d'appareil, renouvellement avec rotation) et Graph (profil, courrier, agenda, OneDrive), au format
de la documentation publique de Microsoft. Tout autre appel réseau fait échouer le test (``common.HTTP.request`` et ``CloudHTTP.call`` bloqués).
Données fictives seulement (DURAND, MARTIN, KERVILAC, example.test) ; identifiant client fictif (GUID de test)."""
import base64
import hashlib
import json
from pathlib import Path
import time
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit, unquote
import urllib.request
import uuid

from agent import microsoft56103 as ms, outlook56103 as ol, onedrive56103 as od, microsoft_web56103 as mw, microsoft5636 as m36
from agent.common import Stop
from agent.desk import Desk
import test_agent as fixtures

CLIENT = '00000000-0000-4000-8000-0000000000a1'
CLAIRE, CAMILLE = 'claire@example.test', 'camille@example.test'
COMPTE_CLAIRE, COMPTE_CAMILLE = 'claire.durand@example.test', 'camille.martin@example.test'
GRAPH = 'https://graph.microsoft.com'
LOGIN = 'https://login.microsoftonline.com/common/oauth2/v2.0/'


def b64url(octets):
    return base64.urlsafe_b64encode(octets).decode().rstrip('=')


class FauxMicrosoft:
    """Microsoft en mémoire. Chaque appel est noté (méthode, url, en-têtes). Jetons d'accès vérifiés à chaque appel Graph (401 sinon)."""

    def __init__(self):
        self.appels, self.codes, self.acces, self.renouvellements, self.appareils = [], {}, {}, {}, {}
        self.n = 0
        self.portees_en_plus = []            # portées accordées en plus par Microsoft (essai du garde-fou)
        self.messages, self.dossiers = {}, {'drafts': 'dossier-brouillons'}
        self.perdre_creation = False         # la création du brouillon réussit mais la réponse se perd
        self.relecture_pas_brouillon = False
        self.refuser_piece = False
        self.fichiers, self.versions_fichiers = {}, {}
        self.evenements = []

    def _json(self, valeur, statut=200, entetes=None):
        return statut, dict(entetes or {}), json.dumps(valeur).encode()

    def _jetons(self, compte, portees):
        self.n += 1
        acces, renouvellement = 'acces-%d-%s' % (self.n, uuid.uuid4().hex), 'renouv-%d-%s' % (self.n, uuid.uuid4().hex)
        self.acces[acces] = {'compte': compte, 'portees': portees}
        self.renouvellements[renouvellement] = {'compte': compte, 'portees': portees, 'valide': True}
        return {'token_type': 'Bearer', 'access_token': acces, 'refresh_token': renouvellement, 'expires_in': 3599,
                'scope': ' '.join(portees + self.portees_en_plus)}

    def consentir(self, url, compte=COMPTE_CLAIRE):
        q = {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}
        self.n += 1
        code = 'code-%d' % self.n
        self.codes[code] = {'defi': q['code_challenge'], 'methode': q['code_challenge_method'], 'redirect': q['redirect_uri'], 'compte': compte,
                            'portees': [p for p in q['scope'].split() if p != 'offline_access'], 'client': q['client_id']}
        return {'state': q['state'], 'code': code}

    def __call__(self, methode, url, entetes, corps):
        self.appels.append((methode, url, dict(entetes)))
        if url.startswith(LOGIN):
            return self._identite(url[len(LOGIN):], {k: v[0] for k, v in parse_qs((corps or b'').decode()).items()})
        acces = (entetes.get('Authorization') or '')[len('Bearer '):]
        if acces not in self.acces:
            return self._json({'error': {'code': 'InvalidAuthenticationToken'}}, 401)
        return self._graph(methode, url[len(GRAPH):], corps, entetes, self.acces[acces])

    # ----- identité
    def _identite(self, point, f):
        if point == 'devicecode':
            self.n += 1
            code_appareil = 'appareil-%d-%s' % (self.n, uuid.uuid4().hex)
            self.appareils[code_appareil] = {'portees': [p for p in f['scope'].split() if p != 'offline_access'], 'reponses': [], 'client': f['client_id']}
            return self._json({'device_code': code_appareil, 'user_code': 'KXR%dQ7' % self.n, 'verification_uri': 'https://microsoft.com/devicelogin',
                               'expires_in': 900, 'interval': 5, 'message': 'Saisissez le code.'})
        g = f.get('grant_type')
        if g == 'authorization_code':
            c = self.codes.pop(f.get('code'), None)
            if not c or c['redirect'] != f.get('redirect_uri') or c['client'] != f.get('client_id') or 'client_secret' in f:
                return self._json({'error': 'invalid_grant'}, 400)
            if c['methode'] != 'S256' or b64url(hashlib.sha256(f.get('code_verifier', '').encode()).digest()) != c['defi']:
                return self._json({'error': 'invalid_grant'}, 400)               # vérificateur PKCE faux
            return self._json(self._jetons(c['compte'], c['portees']))
        if g == 'refresh_token':
            r = self.renouvellements.get(f.get('refresh_token'))
            if not r or not r['valide']:
                return self._json({'error': 'invalid_grant'}, 400)
            r['valide'] = False                                                 # rotation : l'ancien jeton ne sert plus
            return self._json(self._jetons(r['compte'], r['portees']))
        if g == 'urn:ietf:params:oauth:grant-type:device_code':
            a = self.appareils.get(f.get('device_code'))
            if not a:
                return self._json({'error': 'bad_verification_code'}, 400)
            reponse = a['reponses'].pop(0) if a['reponses'] else 'authorization_pending'
            if reponse.startswith('ok:'):
                return self._json(self._jetons(reponse[3:], a['portees']))
            return self._json({'error': reponse}, 400)
        return self._json({'error': 'unsupported_grant_type'}, 400)

    # ----- Graph
    def _graph(self, methode, chemin, corps, entetes, jeton):
        p = urlsplit(chemin)
        route, q = unquote(p.path), {k: v[0] for k, v in parse_qs(p.query).items()}
        if route == '/v1.0/me':
            return self._json({'id': 'id-' + jeton['compte'], 'userPrincipalName': jeton['compte'], 'displayName': 'Fictif'})
        if route == '/v1.0/me/mailFolders/drafts' and methode == 'GET':
            return self._json({'id': self.dossiers['drafts']})
        if route == '/v1.0/me/mailFolders/drafts/messages' and methode == 'POST':
            self.n += 1
            mid = 'AAMkBROUILLON%04d' % self.n
            m = json.loads(corps)
            self.messages[mid] = {**m, 'id': mid, 'isDraft': True, 'parentFolderId': self.dossiers['drafts'], 'attachments': [], 'changeKey': 'ck%d' % self.n}
            if self.perdre_creation:
                self.perdre_creation = False
                return self._json({'error': {'code': 'ServiceUnavailable'}}, 503)
            return self._json(self.messages[mid], 201)
        if route == '/v1.0/me/mailFolders/drafts/messages' and methode == 'GET':
            filtre = q.get('$filter', '')
            ids = [mid for mid, m in self.messages.items() if any("ep/value eq '%s'" % x['value'] in filtre and x['id'] in filtre
                                                                    for x in m.get('singleValueExtendedProperties') or [])]
            return self._json({'value': [{'id': i} for i in ids]})
        if route.startswith('/v1.0/me/messages/'):
            reste = route[len('/v1.0/me/messages/'):].split('/')
            m = self.messages.get(reste[0])
            if not m:
                return self._json({'error': {'code': 'ErrorItemNotFound'}}, 404)
            if len(reste) == 1 and methode == 'GET':
                return self._json({**{k: v for k, v in m.items() if k != 'sessions'}, 'isDraft': m['isDraft'] and not self.relecture_pas_brouillon,
                                   'attachments': [{'id': a['id'], 'name': a['name'], 'size': a['size']} for a in m['attachments']]})
            if reste[1:] == ['attachments'] and methode == 'POST':
                if self.refuser_piece:
                    return self._json({'error': {'code': 'ErrorQuotaExceeded'}}, 413)
                a = json.loads(corps)
                taille = len(base64.b64decode(a['contentBytes']))
                m['attachments'].append({'id': 'pj%d' % len(m['attachments']), 'name': a['name'], 'size': taille + 120})
                return self._json({'id': 'pj'}, 201)
            if reste[1:] == ['attachments', 'createUploadSession'] and methode == 'POST':
                item = json.loads(corps)['AttachmentItem']
                m.setdefault('sessions', {})
                sid = 'S%d' % len(m['sessions'])
                m['sessions'][sid] = {'name': item['name'], 'size': item['size'], 'recu': b''}
                return self._json({'uploadUrl': "https://outlook.office.com/api/v2.0/Users('u')/Messages('%s')/AttachmentSessions('%s')?authtoken=jeton-session" % (reste[0], sid),
                                   'nextExpectedRanges': ['0-']}, 201)
        if route == '/v1.0/me/events' and methode == 'POST':
            self.evenements.append(json.loads(corps))
            return self._json({'id': 'ev-%d' % len(self.evenements), 'iCalUId': 'uid-%d' % len(self.evenements)}, 201)
        if route.startswith('/v1.0/me/drive'):
            return self._drive(methode, route[len('/v1.0/me/drive'):], q, corps, entetes)
        return self._json({'error': {'code': 'NonSimule', 'route': route}}, 400)

    # ----- envoi des grandes pièces (adresse pré-authentifiée d'Outlook)
    def envoi(self, methode, url, entetes, corps):
        self.appels.append((methode, url, dict(entetes)))
        assert 'Authorization' not in entetes and 'authorization' not in {k.lower() for k in entetes}
        mid, sid = url.split("Messages('")[1].split("'")[0], url.split("AttachmentSessions('")[1].split("'")[0]
        m = self.messages[mid]
        s = m['sessions'][sid]
        s['recu'] += corps
        if len(s['recu']) >= s['size']:
            m['attachments'].append({'id': 'pj%d' % len(m['attachments']), 'name': s['name'], 'size': len(s['recu']) + 200})
            return 201, {'Location': 'https://outlook.office.com/x'}, b''
        return 200, {}, json.dumps({'nextExpectedRanges': [str(len(s['recu']))]}).encode()

    # ----- OneDrive
    def _drive(self, methode, route, q, corps, entetes):
        if route.startswith('/root:'):
            chemin, _, suite = route[len('/root:'):].partition(':')
        elif route.startswith('/items/'):
            ident, _, suite = route[len('/items/'):].partition('/')
            chemin = next((c for c, f in self.fichiers.items() if f['id'] == ident), None)
            suite = '/' + suite if suite else ''
            if chemin is None:
                return self._json({'error': {'code': 'itemNotFound'}}, 404)
        else:
            return self._json({'error': {'code': 'NonSimule'}}, 400)
        f = self.fichiers.get(chemin)
        if suite == '/content' and methode == 'PUT':
            if q.get('@microsoft.graph.conflictBehavior') == 'fail' and f:
                return self._json({'error': {'code': 'nameAlreadyExists'}}, 409)
            if 'If-Match' in entetes and (not f or entetes['If-Match'] != f['etag']):
                return self._json({'error': {'code': 'resourceModified'}}, 412)
            self.n += 1
            if f:
                self.versions_fichiers.setdefault(chemin, []).insert(0, {'id': '%d.0' % (len(self.versions_fichiers.get(chemin, [])) + 1), 'donnees': f['donnees']})
            self.fichiers[chemin] = {'id': (f or {}).get('id') or 'it%d' % self.n, 'etag': '"{E%d},1"' % self.n, 'donnees': corps}
            return self._json({'id': self.fichiers[chemin]['id'], 'eTag': self.fichiers[chemin]['etag'], 'size': len(corps)}, 201)
        if not f and suite != ':/children' and suite != '/children':
            return self._json({'error': {'code': 'itemNotFound'}}, 404)
        if suite == '' and methode == 'GET':
            return self._json({'id': f['id'], 'name': chemin.rsplit('/', 1)[-1], 'eTag': f['etag'], 'size': len(f['donnees']), 'file': {},
                               '@microsoft.graph.downloadUrl': 'https://exemple.sharepoint.com/telecharger/%s' % f['id']})
        if suite == '' and methode == 'DELETE':
            if entetes.get('If-Match') != f['etag']:
                return self._json({'error': {'code': 'resourceModified'}}, 412)
            del self.fichiers[chemin]
            return 204, {}, b''
        if suite in (':/children', '/children'):
            enfants = [{'id': x['id'], 'name': c.rsplit('/', 1)[-1], 'eTag': x['etag'], 'size': len(x['donnees']), 'file': {}}
                       for c, x in self.fichiers.items() if c.rsplit('/', 1)[0] == chemin]
            return self._json({'value': enfants})
        if suite == '/versions':
            return self._json({'value': [{'id': v['id'], 'size': len(v['donnees']), 'lastModifiedDateTime': '2026-11-14T10:00:00Z'} for v in self.versions_fichiers.get(chemin, [])]})
        if suite.startswith('/versions/') and suite.endswith('/content'):
            vid = suite.split('/')[2]
            return 302, {'Location': 'https://exemple.sharepoint.com/version/%s/%s' % (f['id'], vid)}, b''
        return self._json({'error': {'code': 'NonSimule'}}, 400)

    def telecharger(self, methode, url, entetes, corps):
        self.appels.append((methode, url, dict(entetes)))
        assert not any(k.lower() == 'authorization' for k in entetes)
        if '/telecharger/' in url:
            ident = url.rsplit('/', 1)[-1]
            return 200, {}, next(f['donnees'] for f in self.fichiers.values() if f['id'] == ident)
        if '/version/' in url:
            ident, vid = url.split('/version/')[1].split('/')
            chemin = next(c for c, f in self.fichiers.items() if f['id'] == ident)
            return 200, {}, next(v['donnees'] for v in self.versions_fichiers[chemin] if v['id'] == vid)
        return 404, {}, b''


class Base(unittest.TestCase):
    client = CLIENT

    def setUp(self):
        self.f = fixtures.EngineTests(methodName='test_observation_has_no_mail_write')
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.f.c['microsoft56103'] = {'client_id': self.client}
        self.desk = Desk(self.f.c)
        self.addCleanup(self.desk.db.close)
        self.ms = FauxMicrosoft()
        self.horloge = [time.time()]
        bloque = AssertionError('appel réseau non simulé')
        for p in (patch.object(ms, 'TRANSPORT', self.ms), patch.object(ms, 'HORLOGE', lambda: self.horloge[0]),
                  patch.object(ol, 'ENVOI_TRANSPORT', self.ms.envoi), patch.object(od, 'TELECHARGEMENT', self.ms.telecharger),
                  patch('agent.common.HTTP.request', side_effect=bloque), patch('agent.cloud5688.CloudHTTP.call', side_effect=bloque)):
            p.start()
            self.addCleanup(p.stop)

    def connecter(self, owner=CLAIRE, compte=COMPTE_CLAIRE, caps=('courrier', 'agenda', 'fichiers')):
        depart = ms.demarrer_pkce(self.desk, owner, list(caps), 50123)
        return ms.terminer_pkce(self.desk, self.ms.consentir(depart['url'], compte))

    def conn(self, owner=CLAIRE):
        return ms.connexion(self.desk, owner)

    def appels_reseau(self):
        return len(self.ms.appels)


# ------------------------------------------------------------------------------------------------ PKCE (poste)
class PKCE(Base):
    def test_M01_pkce_verificateur_defi_et_etat(self):
        depart = ms.demarrer_pkce(self.desk, CLAIRE, ['courrier'], 50123)
        q = {k: v[0] for k, v in parse_qs(urlsplit(depart['url']).query).items()}
        self.assertTrue(depart['url'].startswith('https://login.microsoftonline.com/common/oauth2/v2.0/authorize?'))
        self.assertEqual((q['client_id'], q['code_challenge_method'], q['response_type']), (CLIENT, 'S256', 'code'))
        self.assertEqual(q['redirect_uri'], 'http://localhost:50123/axiorhub/microsoft-retour')
        self.assertNotIn('client_secret', depart['url'])
        self.assertEqual(set(q['scope'].split()), {'offline_access', 'User.Read', 'Mail.ReadWrite'})
        ligne = self.desk.db.execute('SELECT * FROM microsoft56103_etats').fetchone()
        self.assertNotEqual(ligne['etat_hash'], q['state'])                       # état gardé haché
        self.assertEqual(ligne['etat_hash'], hashlib.sha256(q['state'].encode()).hexdigest())
        self.assertTrue(43 <= len(ligne['verificateur']) <= 128)                   # RFC 7636
        self.assertEqual(b64url(hashlib.sha256(ligne['verificateur'].encode()).digest()), q['code_challenge'])
        self.assertEqual(self.appels_reseau(), 0)                                  # aucun appel au départ
        out = ms.terminer_pkce(self.desk, self.ms.consentir(depart['url']))
        self.assertEqual(out['connexion']['etat'], 'active')
        self.assertEqual(out['capacites'], ['courrier'])
        self.assertEqual(out['connexion']['compte'], COMPTE_CLAIRE)

    def test_M02_pkce_etat_inconnu_rejoue_expire(self):
        depart = ms.demarrer_pkce(self.desk, CLAIRE, ['courrier'], 50123)
        retour = self.ms.consentir(depart['url'])
        with self.assertRaisesRegex(Stop, 'microsoft56103_etat_invalide'):
            ms.terminer_pkce(self.desk, {'state': 'etat-forge', 'code': retour['code']})
        self.assertEqual(self.appels_reseau(), 0)                                  # aucun échange de code sur un état inconnu
        ms.terminer_pkce(self.desk, retour)
        with self.assertRaisesRegex(Stop, 'microsoft56103_etat_invalide'):     # rejeu
            ms.terminer_pkce(self.desk, retour)
        depart = ms.demarrer_pkce(self.desk, CAMILLE, ['courrier'], 50123)
        self.horloge[0] += ms.DUREE_ETAT + 1
        n = self.appels_reseau()
        with self.assertRaisesRegex(Stop, 'microsoft56103_etat_expire'):
            ms.terminer_pkce(self.desk, self.ms.consentir(depart['url'], COMPTE_CAMILLE))
        self.assertEqual(self.appels_reseau(), n)
        self.assertNotEqual(self.conn(CAMILLE)['etat'], 'active')

    def test_M03_pkce_mutation_verificateur_altere_refuse(self):
        depart = ms.demarrer_pkce(self.desk, CLAIRE, ['courrier'], 50123)
        self.desk.db.execute("UPDATE microsoft56103_etats SET verificateur='verificateur-altere-' || verificateur")
        self.desk.db.commit()
        with self.assertRaises(Stop):
            ms.terminer_pkce(self.desk, self.ms.consentir(depart['url']))
        self.assertEqual(self.conn()['etat'], 'en_attente')                       # rien n'est activé

    def test_M04_pkce_refus_de_la_personne(self):
        depart = ms.demarrer_pkce(self.desk, CLAIRE, ['courrier'], 50123)
        etat = {k: v[0] for k, v in parse_qs(urlsplit(depart['url']).query).items()}['state']
        with self.assertRaisesRegex(Stop, 'microsoft56103_autorisation_refusee'):
            ms.terminer_pkce(self.desk, {'state': etat, 'error': 'access_denied'})
        with self.assertRaisesRegex(Stop, 'microsoft56103_etat_invalide'):     # l'état est consommé même en cas de refus
            ms.terminer_pkce(self.desk, {'state': etat, 'code': 'code-x'})

    def test_M05_ecoute_locale_ponctuelle(self):
        vus = []
        port = ms.ecouter_retour(self.f.c, minutes=1, traiter=lambda args: (vus.append(args) or ('Compte Microsoft relié', 'ok', True)))
        with urllib.request.urlopen('http://127.0.0.1:%d%s?code=c1&state=e1' % (port, ms.CHEMIN_RETOUR), timeout=5) as r:
            page = r.read().decode()
        self.assertIn('Compte Microsoft relié', page)
        self.assertEqual(vus, [{'code': 'c1', 'state': 'e1'}])
        self.assertNotIn('c1', page)                                               # jamais de code dans la page

    def test_M06_compte_different_refuse(self):
        self.connecter()
        depart = ms.demarrer_pkce(self.desk, CLAIRE, ['agenda'], 50123)
        self.assertIn('Mail.ReadWrite', parse_qs(urlsplit(depart['url']).query)['scope'][0])   # capacités déjà accordées redemandées
        with self.assertRaisesRegex(Stop, 'microsoft56103_compte_different'):
            ms.terminer_pkce(self.desk, self.ms.consentir(depart['url'], COMPTE_CAMILLE))


# ------------------------------------------------------------------------------------------------ code d'appareil (serveur)
class Appareil(Base):
    def _depart(self, owner=CLAIRE):
        out = ms.demarrer_appareil(self.desk, owner, ['courrier'])
        code = next(iter(self.ms.appareils))
        return out, self.ms.appareils[code], code

    def test_M10_attente_slow_down_puis_succes(self):
        out, faux, code = self._depart()
        self.assertEqual((out['mode'], out['adresse'], out['intervalle']), ('appareil', 'https://microsoft.com/devicelogin', 5))
        self.assertTrue(out['code'])
        n = self.appels_reseau()
        self.assertEqual(ms.sonder_appareil(self.desk, CLAIRE, out['flux'])['etat'], 'en_attente')
        self.assertEqual(self.appels_reseau(), n)                                  # trop tôt : aucun appel
        faux['reponses'] = ['authorization_pending', 'slow_down', 'ok:' + COMPTE_CLAIRE]
        self.horloge[0] += 5
        self.assertEqual(ms.sonder_appareil(self.desk, CLAIRE, out['flux'])['etat'], 'en_attente')
        self.horloge[0] += 5
        lent = ms.sonder_appareil(self.desk, CLAIRE, out['flux'])
        self.assertEqual((lent['etat'], lent['intervalle']), ('en_attente', 10))   # slow_down : +5 s
        self.horloge[0] += 6
        n = self.appels_reseau()
        self.assertEqual(ms.sonder_appareil(self.desk, CLAIRE, out['flux'])['etat'], 'en_attente')
        self.assertEqual(self.appels_reseau(), n)                                  # nouvel intervalle respecté
        self.horloge[0] += 5
        fin = ms.sonder_appareil(self.desk, CLAIRE, out['flux'])
        self.assertEqual(fin['etat'], 'accepte')
        self.assertEqual(self.conn()['etat'], 'active')
        self.assertFalse(list((Path(self.f.c['state_dir']) / 'vault' / 'microsoft56103').rglob('appareil-*')))   # code d'appareil effacé
        appel = [a for a in self.ms.appels if a[1].endswith('/token')][-1]
        self.assertNotIn('Authorization', appel[2])

    def test_M11_refus(self):
        out, faux, _ = self._depart()
        faux['reponses'] = ['authorization_declined']
        self.horloge[0] += 5
        self.assertEqual(ms.sonder_appareil(self.desk, CLAIRE, out['flux'])['etat'], 'refuse')
        n = self.appels_reseau()
        self.horloge[0] += 60
        self.assertEqual(ms.sonder_appareil(self.desk, CLAIRE, out['flux'])['etat'], 'refuse')
        self.assertEqual(self.appels_reseau(), n)                                  # arrêt : plus aucun appel
        self.assertNotEqual(self.conn()['etat'], 'active')

    def test_M12_expiration_locale_et_chez_microsoft(self):
        out, faux, _ = self._depart()
        self.horloge[0] += 901
        n = self.appels_reseau()
        self.assertEqual(ms.sonder_appareil(self.desk, CLAIRE, out['flux'])['etat'], 'expire')
        self.assertEqual(self.appels_reseau(), n)
        out, faux, code = ms.demarrer_appareil(self.desk, CLAIRE, ['courrier']), None, None
        faux = list(self.ms.appareils.values())[-1]
        faux['reponses'] = ['expired_token']
        self.horloge[0] += 5
        self.assertEqual(ms.sonder_appareil(self.desk, CLAIRE, out['flux'])['etat'], 'expire')
        out = ms.demarrer_appareil(self.desk, CLAIRE, ['courrier'])
        list(self.ms.appareils.values())[-1]['reponses'] = ['bad_verification_code']
        self.horloge[0] += 5
        self.assertEqual(ms.sonder_appareil(self.desk, CLAIRE, out['flux'])['etat'], 'erreur')

    def test_M13_flux_d_une_autre_personne_introuvable(self):
        out, _, _ = self._depart()
        with self.assertRaisesRegex(Stop, 'microsoft56103_appareil_inconnu'):
            ms.sonder_appareil(self.desk, CAMILLE, out['flux'])

    def test_M14_code_appareil_jamais_en_base(self):
        _, _, code = self._depart()
        brut = Path(self.f.c['state_dir'])
        self.desk.db.commit()
        dump = '\n'.join(self.desk.db.iterdump())
        self.assertNotIn(code, dump)
        for fichier in brut.rglob('*'):
            if fichier.is_file():
                self.assertNotIn(code.encode(), fichier.read_bytes(), str(fichier))


# ------------------------------------------------------------------------------------------------ renouvellement et révocation
class Jetons(Base):
    def test_M20_actualisation_avec_rotation(self):
        self.connecter()
        row = self.conn()
        premier = ms._lire(ms._vault(self.desk, row['id'], 'refresh'))
        self.horloge[0] += 4000
        acces = ms.jeton(self.desk, row['id'])
        self.assertIn(acces, self.ms.acces)
        second = ms._lire(ms._vault(self.desk, row['id'], 'refresh'))
        self.assertNotEqual(premier, second)                                       # nouveau jeton de renouvellement rangé
        self.assertFalse(self.ms.renouvellements[premier]['valide'])
        appel = [a for a in self.ms.appels if a[1].endswith('/token')][-1]
        self.assertNotIn('client_secret', str(appel))

    def test_M21_invalid_grant_a_reconnecter_puis_plus_aucun_appel(self):
        self.connecter()
        row = self.conn()
        for r in self.ms.renouvellements.values():
            r['valide'] = False                                                    # autorisation retirée chez Microsoft
        self.horloge[0] += 4000
        with self.assertRaisesRegex(Stop, 'microsoft56103_reconnexion_requise'):
            ms.jeton(self.desk, row['id'])
        self.assertEqual(self.conn()['etat'], 'a_reconnecter')
        n = self.appels_reseau()
        with self.assertRaisesRegex(Stop, 'microsoft56103_reconnexion_requise'):
            ms.appeler(self.desk, row['id'], 'GET', GRAPH + '/v1.0/me')
        self.assertEqual(self.appels_reseau(), n)
        self.assertEqual([e['etat'] for e in ms.etapes_assistant(self.desk, CLAIRE)][:1], ['a_refaire'])

    def test_M22_deconnexion_efface_et_arrete(self):
        self.connecter()
        row = self.conn()
        flux = ms.demarrer_appareil(self.desk, CLAIRE, ['fichiers'])
        out = ms.deconnecter(self.desk, CLAIRE)
        self.assertEqual(out['revocation'], 'locale_seulement')
        self.assertIn('myapps.microsoft.com', out['message'])
        self.assertFalse(list(ms._vault(self.desk, row['id'], 'x').parent.glob('*.secret')))
        self.assertEqual(self.conn()['generation'], row['generation'] + 1)
        n = self.appels_reseau()
        with self.assertRaisesRegex(Stop, 'microsoft56103_non_connecte'):
            ms.appeler(self.desk, row['id'], 'GET', GRAPH + '/v1.0/me')
        self.horloge[0] += 10
        self.assertEqual(ms.sonder_appareil(self.desk, CLAIRE, flux['flux'])['etat'], 'annule')
        self.assertEqual(self.appels_reseau(), n)

    def test_M23_deconnexion_pendant_la_connexion_rien_n_est_reactive(self):
        depart = ms.demarrer_pkce(self.desk, CLAIRE, ['courrier'], 50123)
        retour = self.ms.consentir(depart['url'])
        ms.deconnecter(self.desk, CLAIRE)
        with self.assertRaises(Stop):
            ms.terminer_pkce(self.desk, retour)
        self.assertEqual(self.conn()['etat'], 'deconnectee')


# ------------------------------------------------------------------------------------------------ garde-fous : jamais d'envoi
class Garde(Base):
    def test_M30_mail_send_jamais_demande(self):
        for caps in (['courrier'], ['courrier', 'agenda', 'fichiers', 'sharepoint']):
            self.assertNotIn('mail.send', ms.portees(caps).lower())
        with patch.dict(ms.CAPACITES, {'courrier': ('Mail.ReadWrite', 'Mail.Send')}):   # mutation : ajout de Mail.Send
            with self.assertRaisesRegex(Stop, 'microsoft56103_portee_interdite'):
                ms.portees(['courrier'])
            with self.assertRaisesRegex(Stop, 'microsoft56103_portee_interdite'):
                ms.demarrer_pkce(self.desk, CLAIRE, ['courrier'], 50123)
        self.assertEqual(self.appels_reseau(), 0)

    def test_M31_jeton_capable_d_envoyer_refuse(self):
        self.ms.portees_en_plus = ['Mail.Send']
        with self.assertRaisesRegex(Stop, 'microsoft56103_portee_interdite_accordee'):
            self.connecter()
        self.assertNotEqual(self.conn()['etat'], 'active')
        self.assertFalse(ms._vault(self.desk, self.conn()['id'], 'access').is_file())
        with patch.object(ms, 'INTERDITES', frozenset()):                          # mutation : garde-fou retiré → la connexion passerait
            self.assertEqual(self.connecter()['connexion']['etat'], 'active')

    def test_M32_adresses_d_envoi_refusees_par_le_transport(self):
        self.connecter()
        row = self.conn()
        n = self.appels_reseau()
        for chemin in ('/v1.0/me/sendMail', '/v1.0/me/messages/AAMkX1234567/send', '/v1.0/users/x/messages/AAMkX1234567/reply',
                       '/v1.0/me/messages/AAMkX1234567/replyAll', '/v1.0/me/messages/AAMkX1234567/forward'):
            with self.assertRaisesRegex(Stop, 'microsoft56103_envoi_interdit'):
                ms.appeler(self.desk, row['id'], 'POST', GRAPH + chemin, {})
        self.assertEqual(self.appels_reseau(), n)
        with patch.object(ms, 'ENVOI', __import__('re').compile(r'(?!)')):        # mutation : sans le garde-fou, l'appel part
            try:
                ms.appeler(self.desk, row['id'], 'POST', GRAPH + '/v1.0/me/sendMail', {})
            except Stop:
                pass
        self.assertEqual(self.appels_reseau(), n + 1)
        with self.assertRaisesRegex(Stop, 'microsoft56103_origine_refusee'):
            ms.appeler(self.desk, row['id'], 'GET', 'https://graph.example.test/v1.0/me')

    def test_M33_identifiant_vide_message_clair_et_aucun_appel(self):
        self.f.c.pop('microsoft56103', None)
        self.desk.c.pop('microsoft56103', None)
        self.assertEqual(ms.CLIENT_ID_INTEGRE, '')                                  # l'éditeur ne l'a pas encore inscrite
        self.assertFalse(ms.disponible(self.desk))
        for fn in (lambda: ms.demarrer_pkce(self.desk, CLAIRE, ['courrier'], 50123), lambda: ms.demarrer_appareil(self.desk, CLAIRE, ['courrier'])):
            with self.assertRaisesRegex(Stop, 'microsoft56103_indisponible'):
                fn()
        self.assertEqual(self.appels_reseau(), 0)
        etapes = ms.etapes_assistant(self.desk, CLAIRE)
        self.assertEqual((etapes[0]['etat'], etapes[0]['detail']), ('indisponible', ms.MESSAGE_INDISPONIBLE))
        self.assertIn('Application intégrée pas encore disponible : utilisez votre propre application.', mw.section_html(self.desk, '/p', CLAIRE, {}))
        with self.assertRaisesRegex(Stop, 'microsoft56103_indisponible'):
            m36.choisir_application(self.desk, {'application': 'integree'}, CLAIRE)
        html = m36.settings_html(self.desk, '/p', {})
        self.assertIn('pas encore disponible', html)
        self.assertIn('data-api="m5636/microsoft/reglages"', html)                 # l'application propre reste réglable
        self.assertEqual(m36.config(self.desk)['application'], 'propre')
        self.assertIsNone(self.conn())

    def test_M34_identifiant_invalide_ignore(self):
        self.desk.c['microsoft56103'] = {'client_id': 'pas-un-guid'}
        self.assertFalse(ms.disponible(self.desk))


# ------------------------------------------------------------------------------------------------ brouillons Outlook
class Brouillons(Base):
    def setUp(self):
        super().setUp()
        self.connecter()

    def _projet(self, pieces=(('Bordereau KERVILAC.pdf', b'%PDF-1.4 bordereau fictif'),)):
        return ol.retenir(self.desk, CLAIRE, 'camille.martin@example.test', '', 'DOS-001 — bordereau', 'Maître MARTIN, voici le bordereau.', pieces)

    def test_M40_retenir_n_appelle_rien(self):
        n = self.appels_reseau()
        p = self._projet()
        self.assertEqual((p['etat'], p['depose']), ('local', False))
        self.assertEqual(self.appels_reseau(), n)

    def test_M41_deposer_avec_pieces_puis_constat(self):
        grande = bytes(range(256)) * (14 * 1024)                                   # 3,5 Mio : session d'envoi
        p = self._projet((('Bordereau KERVILAC.pdf', b'%PDF-1.4 fictif'), ('Pièces DURAND.zip', grande)))
        out = ol.deposer(self.desk, CLAIRE, p['id'], p['version'], 'cle-depot-0001')
        self.assertEqual((out['resultat'], out['etat'], out['depose']), ('depose', 'depose', True))
        (m,) = self.ms.messages.values()
        self.assertTrue(m['isDraft'])
        self.assertEqual(sorted(a['name'] for a in m['attachments']), ['Bordereau KERVILAC.pdf', 'Pièces DURAND.zip'])
        self.assertEqual(m['singleValueExtendedProperties'][0]['id'], ol.MARQUE)
        envois = [a for a in self.ms.appels if a[1].startswith('https://outlook.office.com/')]
        self.assertGreaterEqual(len(envois), 2)
        self.assertTrue(all('Authorization' not in a[2] for a in envois))         # jamais de jeton vers l'adresse pré-authentifiée
        self.assertFalse([a for a in self.ms.appels if __import__('re').search(r'/(sendMail|send)$', urlsplit(a[1]).path, __import__('re').I)])
        again = ol.deposer(self.desk, CLAIRE, p['id'], p['version'], 'cle-depot-0001')   # même clé : rien de plus
        self.assertTrue(again['deja'])
        self.assertEqual(len(self.ms.messages), 1)

    def test_M42_depose_seulement_apres_constat(self):
        self.ms.relecture_pas_brouillon = True
        p = self._projet()
        out = ol.deposer(self.desk, CLAIRE, p['id'], p['version'], 'cle-depot-0002')
        self.assertNotEqual(out['etat'], 'depose')
        self.ms.relecture_pas_brouillon = False
        self.ms.refuser_piece = True
        p = self._projet()
        out = ol.deposer(self.desk, CLAIRE, p['id'], p['version'], 'cle-depot-0003')
        self.assertEqual(out['etat'], 'incomplet')
        self.ms.refuser_piece = False
        out = ol.deposer(self.desk, CLAIRE, p['id'], out['version'], 'cle-depot-0004')   # reprise : pièce manquante jointe, aucun second brouillon
        self.assertEqual(out['etat'], 'depose')
        self.assertEqual(len(self.ms.messages), 2)

    def test_M43_reponse_perdue_a_verifier_puis_retrouve_par_marque(self):
        self.ms.perdre_creation = True
        p = self._projet()
        out = ol.deposer(self.desk, CLAIRE, p['id'], p['version'], 'cle-depot-0005')
        self.assertEqual(out['etat'], 'a_verifier')
        with self.assertRaisesRegex(Stop, 'depot_a_verifier'):
            ol.deposer(self.desk, CLAIRE, p['id'], out['version'], 'cle-depot-0006')
        self.assertEqual(len(self.ms.messages), 1)
        verif = ol.verifier(self.desk, CLAIRE, p['id'])
        self.assertEqual(verif['etat'], 'incomplet')                                # retrouvé, pièce encore à joindre
        out = ol.deposer(self.desk, CLAIRE, p['id'], verif['version'], 'cle-depot-0007')
        self.assertEqual(out['etat'], 'depose')
        self.assertEqual(len(self.ms.messages), 1)                                  # zéro doublon

    def test_M44_geste_reserve_a_la_personne_et_projet_d_autrui(self):
        p = self._projet()
        for attribut, valeur in (('active_job_id', 42), ('origin_context', 'automatic')):
            setattr(self.desk, attribut, valeur)
            try:
                with self.assertRaisesRegex(Stop, 'geste_reserve_a_la_personne'):
                    ol.deposer(self.desk, CLAIRE, p['id'], p['version'], 'cle-depot-%04d' % len(attribut))
            finally:
                setattr(self.desk, attribut, None)
        with self.assertRaisesRegex(Stop, 'brouillon_absent'):
            ol.deposer(self.desk, CAMILLE, p['id'], p['version'], 'cle-depot-0009')
        with self.assertRaisesRegex(Stop, 'version_perimee'):
            ol.deposer(self.desk, CLAIRE, p['id'], p['version'] + 5, 'cle-depot-0010')
        self.assertEqual(len(self.ms.messages), 0)

    def test_M45_mutation_garde_personne(self):
        p = self._projet()
        self.desk.origin_context = 'automatic'
        try:
            with patch.object(ol, '_exiger_personne', lambda desk: None), patch('agent.horaires_ia5689.code_horaire', return_value=''):
                self.assertEqual(ol.deposer(self.desk, CLAIRE, p['id'], p['version'], 'cle-depot-0011')['etat'], 'depose')   # sans le garde-fou, un automatisme déposerait
        finally:
            self.desk.origin_context = None

    def test_M46_lecture_en_texte_brut(self):
        self.ms.messages['AAMkLU0001'] = {'id': 'AAMkLU0001', 'subject': 'RG 24/01234', 'isDraft': False, 'parentFolderId': 'x', 'attachments': [],
                                         'body': {'contentType': 'text', 'content': 'Bonjour Maître.'}}
        m = ol.lire(self.desk, CLAIRE, 'AAMkLU0001')
        self.assertEqual(m['texte'], 'Bonjour Maître.')
        appel = self.ms.appels[-1]
        self.assertEqual(appel[2].get('Prefer'), 'outlook.body-content-type="text"')


# ------------------------------------------------------------------------------------------------ agenda Outlook : intégrée ou propre
class FauxHTTP:
    def __init__(self, reponses):
        self.reponses, self.appels = list(reponses), []

    def request(self, methode, url, data, entetes, limite):
        self.appels.append((methode, url, dict(entetes or {}), data))
        return json.dumps(self.reponses.pop(0)).encode()


class Agenda(Base):
    def test_M50_application_propre_toujours_fonctionnelle(self):
        m36.save_config(self.desk, {'client_id': '11111111-2222-4333-8444-555555555555', 'tenant': 'common', 'push': True, 'pull': True}, CLAIRE)
        self.assertEqual(m36.config(self.desk)['application'], 'propre')
        depart = m36.start(self.desk, {'HTTP_HOST': 'pilote.example.test', 'wsgi.url_scheme': 'https'}, '/p', CLAIRE)
        q = {k: v[0] for k, v in parse_qs(urlsplit(depart['url']).query).items()}
        login = FauxHTTP([{'access_token': 'acces-propre', 'refresh_token': 'renouv-propre', 'expires_in': 3600}])
        graph = FauxHTTP([{'userPrincipalName': COMPTE_CLAIRE}])
        out = m36.finish(self.desk, {'state': q['state'], 'code': 'code-propre'}, http=login, graph=graph)
        self.assertIn(COMPTE_CLAIRE, out['message'])
        self.assertTrue(m36.connected(self.desk))
        self.assertEqual(m36._access(self.desk), 'acces-propre')
        self.assertEqual(self.appels_reseau(), 0)                                  # l'application intégrée n'est pas sollicitée

    def test_M51_bascule_sur_l_application_integree(self):
        self.connecter(caps=('courrier', 'agenda'))
        m36.choisir_application(self.desk, {'application': 'integree'}, CLAIRE)
        self.assertTrue(m36.connected(self.desk))
        self.assertEqual(m36.config(self.desk)['account'], COMPTE_CLAIRE)
        jeton = m36._access(self.desk)
        self.assertIn(jeton, self.ms.acces)
        http = FauxHTTP([{'id': 'ev-1', 'iCalUId': 'uid-1'}])
        ev = {'id': 'loc-1', 'title': 'Audience DURAND', 'starts': '2026-12-01T09:00:00+00:00', 'ends': '2026-12-01T10:00:00+00:00', 'source': 'local'}
        self.assertEqual(m36.push_event(self.desk, ev, http), 'ev-1')
        self.assertEqual(http.appels[0][2]['Authorization'], 'Bearer ' + jeton)
        html = m36.settings_html(self.desk, '/p', {})
        self.assertIn('Application intégrée d’AxiorHub', html)
        self.assertIn('Détacher l’agenda', html)
        out = m36.disconnect(self.desk, CLAIRE)                                       # l'agenda est détaché, le compte reste relié
        self.assertIn('reste relié', out['message'])
        self.assertFalse(m36.connected(self.desk))
        self.assertEqual(self.conn()['etat'], 'active')

    def test_M52_sans_capacite_agenda_non_relie(self):
        self.connecter(caps=('courrier',))
        m36.choisir_application(self.desk, {'application': 'integree'}, CLAIRE)
        self.assertFalse(m36.connected(self.desk))
        with self.assertRaisesRegex(Stop, 'microsoft365_non_configure'):
            m36._access(self.desk)
        m36.choisir_application(self.desk, {'application': 'propre'}, CLAIRE)
        self.assertEqual(m36.config(self.desk)['application'], 'propre')


    def test_M53_seul_compte_avec_agenda_retenu_par_defaut(self):
        self.assertEqual(m36.config(self.desk)['application'], 'integree')          # aucune application propre réglée : l'intégrée par défaut
        self.assertFalse(m36.connected(self.desk))
        self.connecter(caps=('agenda',))
        self.assertTrue(m36.connected(self.desk))
        self.connecter(CAMILLE, COMPTE_CAMILLE, caps=('agenda',))
        self.assertFalse(m36.connected(self.desk))                                  # deux comptes : choix explicite requis
        m36.choisir_application(self.desk, {'application': 'integree'}, CAMILLE)
        self.assertEqual(m36.config(self.desk)['account'], COMPTE_CAMILLE)


# ------------------------------------------------------------------------------------------------ secrets jamais en clair
class Secrets(Base):
    def test_M60_jetons_jamais_en_clair(self):
        self.connecter()
        jetons = list(self.ms.acces) + list(self.ms.renouvellements)
        self.desk.db.commit()
        dump = '\n'.join(self.desk.db.iterdump())
        for j in jetons:
            self.assertNotIn(j, dump)
        for fichier in Path(self.f.c['state_dir']).rglob('*'):
            if fichier.is_file():
                octets = fichier.read_bytes()
                for j in jetons:
                    self.assertNotIn(j.encode(), octets, str(fichier))
        row = self.conn()
        self.assertTrue(ms._vault(self.desk, row['id'], 'access').read_text().startswith('AXIORHUB-VAULT-V1:'))
        self.assertNotIn('acces', json.dumps(ms.vue(row)))
        self.assertNotIn('renouv', json.dumps(mw.etat(self.desk, CLAIRE, {})))

    def test_M61_jeton_long_accepte_par_le_coffre(self):
        self.connecter()
        row = self.conn()
        long = 'x' * 6000                                                           # au-delà de la borne historique de 4 096 caractères
        ms._ecrire(ms._vault(self.desk, row['id'], 'access'), long)
        self.assertEqual(ms._lire(ms._vault(self.desk, row['id'], 'access')), long)


# ------------------------------------------------------------------------------------------------ OneDrive / SharePoint
class OneDrive(Base):
    def setUp(self):
        super().setUp()
        self.connecter()
        self.src = od.SourceOneDrive(self.desk, CLAIRE)

    def test_M70_ecriture_conditionnelle_et_lecture(self):
        v1 = self.src.ecrire('DOS-001/Conclusions.docx', b'version 1')
        put = [a for a in self.ms.appels if a[0] == 'PUT'][-1]
        self.assertIn('conflictBehavior=fail', put[1])
        self.assertIn('/root:/AxiorHub/DOS-001/Conclusions.docx:/content', unquote(put[1]))
        with self.assertRaisesRegex(Stop, 'fichier_existe_deja'):
            self.src.ecrire('DOS-001/Conclusions.docx', b'autre')                   # création exclusive : jamais d'écrasement
        v2 = self.src.ecrire('DOS-001/Conclusions.docx', b'version 2', v1['version'])
        self.assertEqual([a for a in self.ms.appels if a[0] == 'PUT'][-1][2]['If-Match'], v1['version'])
        with self.assertRaisesRegex(Stop, 'version_modifiee'):
            self.src.ecrire('DOS-001/Conclusions.docx', b'version perimee', v1['version'])
        lu = self.src.lire('DOS-001/Conclusions.docx')
        self.assertEqual((lu['donnees'], lu['version']), (b'version 2', v2['version']))
        self.assertEqual([x['nom'] for x in self.src.lister('DOS-001')], ['Conclusions.docx'])
        versions = self.src.versions('DOS-001/Conclusions.docx')
        self.assertEqual(self.src.lire_version('DOS-001/Conclusions.docx', versions[0]['id']), b'version 1')
        with self.assertRaisesRegex(Stop, 'chemin_invalide'):
            self.src.lire('../secret.txt')

    def test_M71_lien_de_telechargement_hors_microsoft_refuse(self):
        self.src.ecrire('DOS-001/Note.txt', b'note')
        with patch.object(od.SourceOneDrive, '_meta', lambda s, c: {'dossier': False, 'nom': 'Note.txt', 'version': 'e', 'id': 'i',
                                                                    '_lien': 'https://pirate.example.test/vol'}):
            with self.assertRaisesRegex(Stop, 'hote_transfert_refuse'):
                self.src.lire('DOS-001/Note.txt')

    def test_M72_source_pour_la_page_fichiers(self):
        self.assertIsNone(od.fabrique(self.desk, {}, (CAMILLE, 'avocat')))         # pas de compte relié : source fermée
        s = od.fabrique(self.desk, {}, (CLAIRE, 'avocat'))
        from agent.fichiers56103 import Source
        self.assertIsInstance(s, Source)
        self.assertEqual(s.racines(), [{'chemin': '/OneDrive', 'nom': 'OneDrive'}])
        etag = s.ecrire('/OneDrive/DOS-001/Acte.docx', b'acte')
        with self.assertRaisesRegex(Stop, 'deja_existant'):
            s.ecrire('/OneDrive/DOS-001/Acte.docx', b'acte bis')
        with self.assertRaisesRegex(Stop, 'version_perimee'):
            s.ecrire('/OneDrive/DOS-001/Acte.docx', b'acte ter', '"{perime},1"')
        self.assertEqual(s.lire('/OneDrive/DOS-001/Acte.docx', 1000), b'acte')
        self.assertEqual([e['chemin'] for e in s.lister('/OneDrive/DOS-001')], ['/OneDrive/DOS-001/Acte.docx'])
        s.corbeille('/OneDrive/DOS-001/Acte.docx', etag)
        with self.assertRaisesRegex(Stop, 'introuvable'):
            s.lire('/OneDrive/DOS-001/Acte.docx', 1000)
        with self.assertRaisesRegex(Stop, 'introuvable'):
            s.lister('/Nextcloud/DOS-001')


# ------------------------------------------------------------------------------------------------ routes
class Routes(Base):
    def test_M80_routes_declarees_et_roles(self):
        from agent import standalone_auth
        for role in ('administrateur', 'avocat', 'assistant'):
            self.assertTrue(standalone_auth.allowed(role, 'POST', '/api440/m56103/microsoft/brouillon/deposer'))
            self.assertTrue(mw.allowed(role, 'GET', 'm56103/microsoft/etat'))
        self.assertFalse(mw.allowed('lecteur', 'POST', 'm56103/microsoft/connexion'))
        self.assertFalse(mw.allowed('avocat', 'POST', 'm56103/microsoft/envoyer'))
        from agent import web440
        self.assertIn('/static/v56103-m.js', web440.STATIC)

    def test_M81_reponses_neutres_et_privees(self):
        self.connecter()
        p = ol.retenir(self.desk, CLAIRE, 'camille.martin@example.test', '', 'Objet', 'Texte')
        corps = json.dumps({'id': p['id'], 'version': p['version'], 'cle': 'cle-route-0001'}).encode()
        import io
        env = {'REQUEST_METHOD': 'POST', 'HTTP_ORIGIN': 'https://cabinet.example.test', 'HTTP_X_CSRF_TOKEN': 'csrf', 'CONTENT_TYPE': 'application/json',
               'CONTENT_LENGTH': str(len(corps)), 'wsgi.input': io.BytesIO(corps)}
        with patch('agent.web567.actor', return_value=(CAMILLE, 'avocat')):
            out = mw.api(env, self.desk, {'origin': 'https://cabinet.example.test', 'csrf': 'csrf'}, '/p', 'm56103/microsoft/brouillon/deposer', {}, 'POST')
        self.assertEqual(out['status'], '404 Not Found')                           # projet d'autrui : même réponse qu'un absent
        self.assertIn(('Cache-Control', 'private, no-store'), out['headers'])
        n = self.appels_reseau()
        with patch('agent.web567.actor', return_value=(CLAIRE, 'avocat')):
            out = mw.api({}, self.desk, {}, '/p', 'm56103/microsoft/etat', {}, 'GET')
        self.assertEqual(json.loads(out['body'])['connexion']['etat'], 'active')
        self.assertEqual(self.appels_reseau(), n)                                  # lecture : aucun appel à Microsoft


if __name__ == '__main__':
    unittest.main()
