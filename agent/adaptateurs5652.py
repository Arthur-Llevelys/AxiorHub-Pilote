"""5.6.52 (audit 5.6.51, I01 à I15) : adaptateurs des connexions API — un contrat d'authentification, des capacités, des erreurs normalisées.

Chaque adaptateur reçoit un contexte (paramètres non secrets, accès aux secrets au dernier moment, environnement) et expose :
``identity()`` (lecture de test, sans effet) et ``call(capacité, paramètres)``. Aucun secret ne sort d'un adaptateur ; aucune adresse
n'est choisie par le modèle (hôtes fixés par fournisseur et environnement). Les contrats suivent les documentations officielles
consultées le 16 octobre 2026 ; les éléments non publiés (Maileva, production AR24, envoi SuperPDP, Chorus Pro) sont signalés
« à qualifier » et restent réglables. Aucun appel authentifié réel n'a été fait pendant le développement.
"""
import base64
from datetime import datetime, timedelta, timezone
import hashlib
import json
import secrets
import time
from urllib.parse import quote, urlencode, urlsplit
from zoneinfo import ZoneInfo

from .common import HTTP, Stop

# Hôtes officiels par fournisseur et environnement (« test » = bac à sable / démonstration, « production »).
ENV_URLS = {
    'ar24': {'test': 'https://sandbox.ar24.fr/api', 'production': 'https://app.ar24.fr/api'},
    'youtrust': {'test': 'https://api-sandbox.yousign.app/v3', 'production': 'https://api.yousign.app/v3'},
    'legifrance_piste': {'test': 'https://sandbox-api.piste.gouv.fr/dila/legifrance/lf-engine-app', 'production': 'https://api.piste.gouv.fr/dila/legifrance/lf-engine-app'},
    'judilibre_piste': {'test': 'https://sandbox-api.piste.gouv.fr/cassation/judilibre/v1.0', 'production': 'https://api.piste.gouv.fr/cassation/judilibre/v1.0'},
    'piste_oauth': {'test': 'https://sandbox-oauth.piste.gouv.fr/api/oauth/token', 'production': 'https://oauth.piste.gouv.fr/api/oauth/token'},
    'pappers_justice': {'test': 'https://api-justice.pappers.fr/v1', 'production': 'https://api-justice.pappers.fr/v1'},
    'pennylane': {'test': 'https://app.pennylane.com/api/external/v2', 'production': 'https://app.pennylane.com/api/external/v2'},
    'docusign': {'test': 'https://account-d.docusign.com', 'production': 'https://account.docusign.com'},
    'superpdp': {'test': 'https://api.superpdp.tech', 'production': 'https://api.superpdp.tech'},
    'b2brouter': {'test': 'https://api-staging.b2brouter.net', 'production': 'https://api.b2brouter.net'},
    'gocardless': {'test': 'https://api-sandbox.gocardless.com', 'production': 'https://api.gocardless.com'},
    'chorus_pro': {'test': 'https://sandbox-api.piste.gouv.fr', 'production': 'https://api.piste.gouv.fr'},
    'maileva': {'test': 'https://api.sandbox.maileva.net/mail/v2', 'production': 'https://api.maileva.com/mail/v2'},
    'maileva_auth': {'test': 'https://connexion.sandbox.maileva.net/auth/realms/services/protocol/openid-connect/token',
                     'production': 'https://connexion.maileva.com/auth/realms/services/protocol/openid-connect/token'},
    'microsoft_graph': {'test': 'https://graph.microsoft.com/v1.0', 'production': 'https://graph.microsoft.com/v1.0'},
    'microsoft_login': {'test': 'https://login.microsoftonline.com', 'production': 'https://login.microsoftonline.com'},
    'google_workspace': {'test': 'https://www.googleapis.com', 'production': 'https://www.googleapis.com'},
    'axonaut': {'test': 'https://axonaut.com/api/v2', 'production': 'https://axonaut.com/api/v2'},
    'recherche_entreprises': {'test': 'https://recherche-entreprises.api.gouv.fr', 'production': 'https://recherche-entreprises.api.gouv.fr'},
}
GOOGLE_HOSTS = ('https://www.googleapis.com', 'https://gmail.googleapis.com', 'https://oauth2.googleapis.com', 'https://openidconnect.googleapis.com')


def url_of(key, env):
    return ENV_URLS[key]['production' if env == 'production' else 'test']


class Context:
    """Contexte d'un appel : paramètres non secrets, lecture des secrets au dernier moment, jetons OAuth et fabrique HTTP."""

    def __init__(self, provider, env, params, secret, token=None, save_token=None, http=None, timezone_name='Europe/Paris'):
        self.provider, self.env, self.params = provider, env, dict(params or {})
        self._secret, self._token, self._save_token = secret, token, save_token
        self._http = http
        self.tz = timezone_name
        self._clients = {}

    def secret(self, name):
        value = self._secret(name)
        if not value:
            raise Stop('secret_manquant:' + name)
        return value

    def optional_secret(self, name):
        try:
            return self._secret(name) or ''
        except Stop:
            return ''

    def http(self, base):
        parts = urlsplit(base)
        origin = '%s://%s' % (parts.scheme, parts.netloc)
        if self._http:
            return self._http(origin)
        if origin not in self._clients:
            self._clients[origin] = HTTP(origin, timeout=60)
        return self._clients[origin]

    def oauth_token(self):
        return self._token() if self._token else None

    def store_token(self, tok):
        if self._save_token:
            self._save_token(tok)


def _multipart(fields, files):
    boundary = 'axh' + secrets.token_hex(12)
    out = []
    for name, value in fields.items():
        out.append(('--%s\r\nContent-Disposition: form-data; name="%s"\r\n\r\n%s\r\n' % (boundary, name, value)).encode('utf-8'))
    for name, (filename, data, ctype) in files.items():
        out.append(('--%s\r\nContent-Disposition: form-data; name="%s"; filename="%s"\r\nContent-Type: %s\r\n\r\n' % (
            boundary, name, filename.replace('"', ''), ctype)).encode('utf-8') + data + b'\r\n')
    out.append(('--%s--\r\n' % boundary).encode())
    return b''.join(out), 'multipart/form-data; boundary=' + boundary


class Adapter:
    provider = ''
    base_key = ''
    auth = 'bearer'

    def __init__(self, ctx):
        self.ctx = ctx

    @property
    def base(self):
        return url_of(self.base_key or self.provider, self.ctx.env)

    def headers(self):
        return {}

    def request(self, method, path, body=None, form=None, multipart=None, raw=False, headers=None, url=None, limit=20_000_000, accept='application/json'):
        target = url or (self.base + path)
        hdr = {'Accept': accept, **self.headers(), **(headers or {})}
        data = None
        if body is not None:
            data = json.dumps(body).encode('utf-8')
            hdr['Content-Type'] = 'application/json'
        elif form is not None:
            data = urlencode(form).encode()
            hdr['Content-Type'] = 'application/x-www-form-urlencoded'
        elif multipart is not None:
            data, hdr['Content-Type'] = _multipart(*multipart)
        out = self.ctx.http(target).request(method, target, data, hdr, limit)
        if raw:
            return out
        if not out:
            return {}
        try:
            return json.loads(out)
        except ValueError:
            raise Stop('reponse_fournisseur_invalide') from None

    def identity(self):
        raise Stop('test_non_disponible')

    def call(self, capability, params):
        fn = getattr(self, 'cap_' + capability.replace('.', '_'), None)
        if not fn:
            raise Stop('capacite_non_codee')
        return fn(params or {})


# ------------------------------------------------------------------------------------------------ OAuth commun
def pkce_pair():
    verifier = secrets.token_urlsafe(64)[:96]
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')
    return verifier, challenge


class OAuthCodeMixin:
    """Jetons d'accès obtenus par consentement (code + PKCE) ; renouvelés par le service des connexions sous verrou."""
    token_url = ''
    authorize_url = ''

    def bearer(self):
        tok = self.ctx.oauth_token()
        if not tok or not tok.get('access_token'):
            raise Stop('connexion_a_autoriser')
        return tok['access_token']

    def headers(self):
        return {'Authorization': 'Bearer ' + self.bearer()}

    def exchange(self, form):
        client_secret = self.ctx.optional_secret('client_secret')
        if client_secret:
            form = {**form, 'client_secret': client_secret}
        out = self.ctx.http(self.token_url).request('POST', self.token_url, urlencode(form).encode(),
                                                   {'Content-Type': 'application/x-www-form-urlencoded', 'Accept': 'application/json'}, 500_000)
        try:
            tok = json.loads(out)
        except ValueError:
            raise Stop('reponse_fournisseur_invalide') from None
        if not tok.get('access_token'):
            raise Stop('autorisation_refusee')
        tok['obtained'] = time.time()
        return tok


class ClientCredentialsMixin:
    """Jeton applicatif (client_credentials) mis en cache le temps de sa validité."""
    token_url = ''
    _cache = {}

    def client_token(self, scope='openid'):
        key = (self.provider, self.ctx.env, self.ctx.params.get('client_id', ''))
        cached = self._cache.get(key)
        if cached and cached[1] > time.time() + 30:
            return cached[0]
        form = {'grant_type': 'client_credentials', 'client_id': self.ctx.params.get('client_id', ''), 'client_secret': self.ctx.secret('client_secret')}
        if scope:
            form['scope'] = scope
        out = self.ctx.http(self.token_url).request('POST', self.token_url, urlencode(form).encode(),
                                                   {'Content-Type': 'application/x-www-form-urlencoded', 'Accept': 'application/json'}, 500_000)
        try:
            data = json.loads(out)
            token = data['access_token']
        except (ValueError, KeyError, TypeError):
            raise Stop('jeton_fournisseur_invalide') from None
        self._cache[key] = (token, time.time() + int(data.get('expires_in', 1800)))
        return token


# ------------------------------------------------------------------------------------------------ I01 AR24
class AR24(Adapter):
    """Jeton (paramètre ``token``) et clé privée distincts ; la date signée (AES-256-CBC) est envoyée dans l'en-tête ``signature`` ;
    les réponses réussies sont chiffrées (clé = SHA-256(date + clé privée)[:32]) ; un HTTP 200 ne prouve pas le succès : le corps est
    contrôlé. La date suit l'heure de Paris. Hôte de production à confirmer avec AR24 lors de l'ouverture de l'accès."""
    provider = 'ar24'

    def _date(self):
        return datetime.now(ZoneInfo('Europe/Paris')).strftime('%Y-%m-%d %H:%M:%S')

    @staticmethod
    def signature(private_key, date):
        from cryptography.hazmat.primitives import padding
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        hashed = hashlib.sha256(private_key.encode()).hexdigest()
        iv = hashlib.sha256(hashed.encode()).hexdigest()[:16].encode()
        padder = padding.PKCS7(128).padder()
        data = padder.update(date.encode()) + padder.finalize()
        enc = Cipher(algorithms.AES(hashed[:32].encode()), modes.CBC(iv)).encryptor()
        return base64.b64encode(enc.update(data) + enc.finalize()).decode()

    @staticmethod
    def decrypt(private_key, date, payload):
        from cryptography.hazmat.primitives import padding
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        key = hashlib.sha256((date + private_key).encode()).hexdigest()[:32].encode()
        iv = hashlib.sha256(hashlib.sha256(private_key.encode()).hexdigest().encode()).hexdigest()[:16].encode()
        dec = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
        raw = dec.update(base64.b64decode(payload)) + dec.finalize()
        unpad = padding.PKCS7(128).unpadder()
        return unpad.update(raw) + unpad.finalize()

    def ar24(self, method, path, params=None, files=None):
        date = self._date()
        pk = self.ctx.secret('private_key')
        query = {'token': self.ctx.secret('token'), 'date': date, **(params or {})}
        hdr = {'signature': self.signature(pk, date), 'Accept': 'application/json'}
        if method == 'GET':
            out = self.ctx.http(self.base).request('GET', self.base + path + '?' + urlencode(query), None, hdr, 20_000_000)
        else:
            data, ctype = _multipart(query, files or {}) if files else (urlencode(query).encode(), 'application/x-www-form-urlencoded')
            out = self.ctx.http(self.base).request('POST', self.base + path, data, {**hdr, 'Content-Type': ctype}, 20_000_000)
        try:
            body = json.loads(out)
        except ValueError:
            body = None
        if isinstance(body, dict) and body.get('status') in ('maintenance', 'ERROR', 'error'):
            raise Stop('ar24_' + str(body.get('slug') or body.get('status'))[:60])
        if isinstance(body, dict) and 'result' in body and isinstance(body['result'], (dict, list)):
            return body['result']
        payload = body.get('result') if isinstance(body, dict) else out.decode('utf-8', 'replace').strip().strip('"')
        try:
            return json.loads(self.decrypt(pk, date, payload))
        except Exception:   # noqa: BLE001 — réponse illisible : aucune conclusion de succès
            raise Stop('reponse_fournisseur_invalide') from None

    def identity(self):
        user = self.ar24('GET', '/user', {'email': self.ctx.params.get('sender_account', '')})
        return {'compte': user.get('email') or user.get('name') or '', 'id_user': str(user.get('id') or ''), 'societe': user.get('company') or ''}

    def cap_registered_mail_send_approved(self, p):
        """Envoi d'une LRE validée : pièce jointe déposée, puis envoi ; référence distante et état retournés."""
        ident = self.identity()
        files = {}
        att = self.ar24('POST', '/attachment', {'id_user': ident['id_user']}, {'file': (p['filename'], p['content'], 'application/pdf')})
        fields = {'id_user': ident['id_user'], 'to_email': p['to_email'], 'dest_statut': p.get('dest_statut', 'professionnel'),
                  'attachment[0]': str(att.get('file_id') or ''), 'ref_dossier': p.get('reference', '')[:60]}
        if fields['dest_statut'] == 'particulier':
            fields.update(to_lastname=p.get('to_lastname', ''), to_firstname=p.get('to_firstname', ''))
        else:
            fields['to_company'] = p.get('to_company', '')
        if p.get('content_text'):
            fields['content'] = p['content_text'][:5000]
        if p.get('eidas'):
            fields['eidas'] = '1'
        mail = self.ar24('POST', '/mail', fields, files)
        return {'remote_ref': str(mail.get('id') or ''), 'etat': str(mail.get('status') or 'sent'), 'brut': {k: mail.get(k) for k in ('id', 'status', 'date')}}

    def cap_registered_mail_status(self, p):
        mail = self.ar24('GET', '/mail', {'id': p['remote_ref']})
        proofs = {k: mail.get(k) for k in ('proof_ev_url', 'proof_ar_url', 'proof_ng_url', 'proof_rf_url', 'proof_bc_url') if mail.get(k)}
        return {'etat': str(mail.get('status') or ''), 'preuves': proofs}

    def cap_registered_mail_proofs(self, p):
        status = self.cap_registered_mail_status(p)
        out = {}
        for name, link in status['preuves'].items():
            if urlsplit(link).netloc != urlsplit(self.base).netloc:
                continue                                    # hôte de téléchargement non choisi par le fournisseur courant : refusé
            out[name] = self.ctx.http(link).request('GET', link, None, {}, 20_000_000)
        return {'etat': status['etat'], 'fichiers': out}


# ------------------------------------------------------------------------------------------------ I02 Youtrust (Yousign v3)
class Youtrust(Adapter):
    provider = 'youtrust'

    def headers(self):
        return {'Authorization': 'Bearer ' + self.ctx.secret('api_key')}

    def identity(self):
        out = self.request('GET', '/signature_requests?limit=1')
        return {'compte': 'clé API valide', 'demandes_visibles': len(out.get('data') or [])}

    def cap_signature_send_approved(self, p):
        step = p.get('_step') or (lambda *a, **k: None)      # 5.6.53 (F04) : chaque étape réussie est journalisée aussitôt
        req = self.request('POST', '/signature_requests', {'name': p['title'][:128], 'delivery_mode': 'email', 'external_id': p['reference'][:128],
                                                           'timezone': 'Europe/Paris'})
        rid = req['id']
        step('creation', rid)
        doc = self.request('POST', '/signature_requests/%s/documents' % rid,
                           multipart=({'nature': 'signable_document', 'parse_anchors': 'false'}, {'file': (p['filename'], p['content'], 'application/pdf')}))
        step('document', doc.get('id', ''))
        for i, s in enumerate(p['signers']):
            out = self.request('POST', '/signature_requests/%s/signers' % rid, {
                'info': {'first_name': s['first_name'], 'last_name': s['last_name'], 'email': s['email'], 'locale': 'fr'},
                'signature_level': 'electronic_signature', 'signature_authentication_mode': s.get('authentication', 'otp_email'),
                'fields': [{'document_id': doc['id'], 'type': 'signature', 'page': int(p.get('page', 1)), 'x': 70 + 160 * (i % 3), 'y': 700}]})
            step('signataire', (out or {}).get('id', ''), s['email'])
        act = self.request('POST', '/signature_requests/%s/activate' % rid, {})
        step('activation', rid)
        return {'remote_ref': rid, 'etat': act.get('status') or 'ongoing'}

    def cap_signature_status(self, p):
        out = self.request('GET', '/signature_requests/' + quote(p['remote_ref']))
        return {'etat': out.get('status', ''), 'signataires': [{'id': s.get('id'), 'etat': s.get('status')} for s in (out.get('signers') or [])]}

    def cap_signature_signed_document(self, p):
        st = self.cap_signature_status(p)
        if st['etat'] != 'done':
            raise Stop('signature_non_terminee')
        return {'etat': 'done', 'fichiers': {'document_signe.pdf': self.request('GET', '/signature_requests/%s/documents/download' % quote(p['remote_ref']), raw=True,
                                                                                accept='application/pdf'),
                                            'piste_audit.pdf': self.request('GET', '/signature_requests/%s/audit_trails/download' % quote(p['remote_ref']), raw=True,
                                                                            accept='application/pdf')}}

    def find_by_reference(self, reference):
        out = self.request('GET', '/signature_requests?external_id=' + quote(reference))
        items = [x for x in out.get('data') or [] if x.get('external_id') == reference]
        return items[0]['id'] if items else ''


# ------------------------------------------------------------------------------------------------ I03 / I04 PISTE
class PisteLegal(ClientCredentialsMixin, Adapter):
    @property
    def token_url(self):
        return url_of('piste_oauth', self.ctx.env)

    def headers(self):
        return {'Authorization': 'Bearer ' + self.client_token('openid')}


class Legifrance(PisteLegal):
    provider = 'legifrance_piste'

    def identity(self):
        out = self.cap_law_verify_reference({'code': 'Code civil', 'number': '1240'})
        if not out.get('trouve'):
            raise Stop('lecture_test_sans_resultat')
        return {'compte': 'application PISTE', 'lecture': 'article 1240 du Code civil lu'}

    def cap_law_search(self, p):
        body = {'fond': 'CODE_DATE', 'recherche': {'champs': [{'typeChamp': 'ALL', 'operateur': 'ET', 'criteres': [
            {'typeRecherche': 'UN_DES_MOTS', 'valeur': p['query'][:200], 'operateur': 'ET'}]}],
            'filtres': ([{'facette': 'NOM_CODE', 'valeurs': [p['code']]}] if p.get('code') else []) +
                       ([{'facette': 'DATE_VERSION', 'singleDate': int(datetime.fromisoformat(p['date']).timestamp() * 1000)}] if p.get('date') else []),
            'pageNumber': 1, 'pageSize': min(int(p.get('limit', 5)), 10), 'operateur': 'ET', 'sort': 'PERTINENCE', 'typePagination': 'ARTICLE'}}
        out = self.request('POST', '/search', body)
        res = []
        for r in out.get('results') or []:
            for s in r.get('sections') or []:
                for x in s.get('extracts') or []:
                    res.append({'id': x.get('id'), 'titre': '%s, article %s' % ((r.get('titles') or [{}])[0].get('title', ''), x.get('num', '')),
                                'extrait': str(x.get('values') or '')[:400], 'url': 'https://www.legifrance.gouv.fr/codes/article_lc/' + str(x.get('id', ''))})
        return {'resultats': res[:10], 'total': out.get('totalResultNumber')}

    def cap_law_read_version(self, p):
        out = self.request('POST', '/consult/getArticle', {'id': p['id']})
        a = out.get('article') or {}
        return {'id': a.get('id'), 'numero': a.get('num'), 'etat': a.get('etat'), 'texte': str(a.get('texte') or '')[:20000],
                'debut': a.get('dateDebut'), 'fin': a.get('dateFin'), 'url': 'https://www.legifrance.gouv.fr/codes/article_lc/' + str(a.get('id', ''))}

    def cap_law_verify_reference(self, p):
        """Article identifié par (code, numéro) : recherche exacte du numéro filtrée sur le code, puis lecture de ses versions."""
        import re
        from .common import fold
        search = {'fond': 'CODE_DATE', 'recherche': {
            'champs': [{'typeChamp': 'NUM_ARTICLE', 'operateur': 'ET', 'criteres': [{'typeRecherche': 'EXACTE', 'valeur': p['number'], 'operateur': 'ET'}]}],
            'filtres': [{'facette': 'NOM_CODE', 'valeurs': [p['code']]}], 'pageNumber': 1, 'pageSize': 10, 'operateur': 'ET', 'sort': 'PERTINENCE',
            'typePagination': 'ARTICLE'}}
        found = self.request('POST', '/search', search)
        ids = [x['id'] for r in found.get('results') or [] for s in r.get('sections') or [] for x in s.get('extracts') or []
               if x.get('id') and fold(str(x.get('num', '')).replace(' ', '')) == fold(str(p['number']))]
        if not ids:
            return {'trouve': False, 'versions': [], 'date_source': datetime.now(timezone.utc).date().isoformat()}
        art = self.request('POST', '/consult/getArticle', {'id': ids[0]}).get('article') or {}
        versions = [{'id': v.get('id'), 'etat': str(v.get('etat', '')).upper(), 'debut': v.get('dateDebut'), 'fin': v.get('dateFin'),
                     'texte': re.sub(r'<[^>]+>', ' ', str(v.get('texte') or ''))[:4000],
                     'url': 'https://www.legifrance.gouv.fr/codes/article_lc/' + str(v.get('id', ''))} for v in (art.get('articleVersions') or [art])]
        return {'trouve': True, 'versions': versions[:5], 'date_source': datetime.now(timezone.utc).date().isoformat()}


class Judilibre(PisteLegal):
    provider = 'judilibre_piste'

    def identity(self):
        out = self.request('GET', '/healthcheck')
        return {'compte': 'application PISTE', 'service': str(out.get('status') or 'disponible')}

    def cap_case_law_search(self, p):
        out = self.request('GET', '/search?' + urlencode({'query': p['query'][:200], 'page_size': min(int(p.get('limit', 5)), 10)}))
        return {'resultats': [{'id': r.get('id'), 'juridiction': r.get('jurisdiction'), 'chambre': r.get('chamber'), 'date': r.get('decision_date'),
                               'numero': r.get('number'), 'sommaire': str(r.get('summary') or '')[:500],
                               'url': 'https://www.courdecassation.fr/decision/' + str(r.get('id', ''))} for r in out.get('results') or []],
                'total': out.get('total'), 'limite': 'Décisions disponibles en données ouvertes seulement ; une absence de résultat ne prouve pas l’inexistence.'}

    def cap_case_law_read(self, p):
        r = self.request('GET', '/decision?' + urlencode({'id': p['id']}))
        return {'id': r.get('id'), 'juridiction': r.get('jurisdiction'), 'date': r.get('decision_date'), 'numero': r.get('number'),
                'texte': str(r.get('text') or '')[:60000], 'url': 'https://www.courdecassation.fr/decision/' + str(r.get('id', ''))}

    def cap_case_law_verify_reference(self, p):
        """Décision retrouvée par son numéro exact (pourvoi ou RG) ; jamais par sa seule date."""
        number = str(p.get('pourvoi') or p.get('rg') or '').strip()
        if not number:
            raise Stop('identifiant_decision_requis')
        out = self.request('GET', '/search?' + urlencode({'query': '"%s"' % number, 'page_size': 10}))
        matches = [r for r in out.get('results') or [] if number in {str(n).replace(' ', '') for n in [r.get('number', '')] + list(r.get('numbers') or [])}]
        return {'trouve': bool(matches), 'decisions': [{'id': r.get('id'), 'date': r.get('decision_date'), 'chambre': r.get('chamber'),
                                                        'url': 'https://www.courdecassation.fr/decision/' + str(r.get('id', ''))} for r in matches[:5]]}


# ------------------------------------------------------------------------------------------------ I05 Pappers Justice
class PappersJustice(Adapter):
    provider = 'pappers_justice'

    def headers(self):
        return {'Authorization': 'Bearer ' + self.ctx.secret('api_key')}

    def identity(self):
        out = self.request('GET', '/recherche/article?' + urlencode({'q': 'article 1240 code civil', 'par_page': 1}))
        return {'compte': 'clé API valide', 'resultats_test': len(out.get('resultats') or [])}

    def cap_legal_search(self, p):
        if p.get('kind') == 'article':
            out = self.request('GET', '/recherche/article?' + urlencode({'q': p['query'][:300], 'par_page': min(int(p.get('limit', 5)), 10)}))
        else:
            out = self.request('GET', '/recherche/decision?' + urlencode({'q': p['query'][:300]}))
        return {'resultats': (out.get('resultats') or [])[:10], 'total': out.get('total')}

    def cap_legal_read(self, p):
        path = '/article/' if p.get('kind') == 'article' else '/decision/'
        return self.request('GET', path + quote(str(p['id'])))

    def cap_external_legal_ai_ask_authorized(self, p):
        out = self.request('POST', '/question-juridique/generer', {'question': p['question'][:5000], 'mode': p.get('mode', 'short')})
        out['statut'] = 'réponse d’une IA externe (Pappers Justice) : à vérifier sur les sources citées ; pas une source primaire'
        return out


# ------------------------------------------------------------------------------------------------ I06 Pennylane
class Pennylane(Adapter):
    provider = 'pennylane'

    def headers(self):
        return {'Authorization': 'Bearer ' + self.ctx.secret('api_token')}

    def identity(self):
        me = self.request('GET', '/me')
        company = me.get('company') or {}
        expected = str(self.ctx.params.get('company_id') or '')
        if expected and str(company.get('id') or '') != expected:
            raise Stop('societe_incorrecte')
        return {'compte': (me.get('user') or {}).get('email', ''), 'societe': company.get('name', ''), 'societe_id': str(company.get('id') or '')}

    def cap_billing_clients(self, p):
        flt = json.dumps([{'field': 'name', 'operator': 'search', 'value': p['query'][:120]}]) if p.get('query') else ''
        out = self.request('GET', '/customers' + ('?filter=' + quote(flt) if flt else ''))
        return {'clients': [{'id': c.get('id'), 'nom': c.get('name'), 'reference': c.get('external_reference')} for c in out.get('items') or []][:20]}

    def cap_billing_prepare(self, p):
        """Facture en BROUILLON (``draft: true``) ; aucune émission définitive depuis AxiorHub."""
        out = self.request('POST', '/customer_invoices', {'customer_id': p['customer_id'], 'date': p['date'], 'deadline': p['deadline'],
                                                          'invoice_lines': p['lines'], 'external_reference': p['reference'][:60], 'draft': True})
        return {'remote_ref': str(out.get('id') or ''), 'etat': out.get('status', 'draft'), 'montant': out.get('currency_amount')}

    def cap_billing_read_status(self, p):
        out = self.request('GET', '/customer_invoices/' + quote(str(p['remote_ref'])))
        return {'etat': out.get('status'), 'montant': out.get('currency_amount'), 'reste_du': out.get('remaining_amount'), 'numero': out.get('invoice_number')}

    def cap_billing_read_payments(self, p):
        out = self.request('GET', '/customer_invoices/%s/matched_transactions' % quote(str(p['remote_ref'])))
        return {'paiements': [{'date': t.get('date'), 'montant': t.get('amount'), 'libelle': t.get('label')} for t in out.get('items') or []]}


# ------------------------------------------------------------------------------------------------ I07 DocuSign
class DocuSign(OAuthCodeMixin, Adapter):
    provider = 'docusign'

    @property
    def token_url(self):
        return self.base + '/oauth/token'

    @property
    def authorize_url(self):
        return self.base + '/oauth/auth'

    def userinfo(self):
        return self.request('GET', '/oauth/userinfo')

    def _account(self):
        info = self.userinfo()
        wanted = str(self.ctx.params.get('account_id') or '')
        accounts = info.get('accounts') or []
        acc = next((a for a in accounts if not wanted or a.get('account_id') == wanted), None) or (accounts[0] if accounts else None)
        if not acc or urlsplit(acc.get('base_uri', '')).scheme != 'https' or not urlsplit(acc.get('base_uri', '')).netloc.endswith('docusign.net'):
            raise Stop('compte_docusign_introuvable')
        return acc['account_id'], acc['base_uri'].rstrip('/') + '/restapi/v2.1/accounts/' + acc['account_id']

    def identity(self):
        info = self.userinfo()
        return {'compte': info.get('email', ''), 'comptes_docusign': [a.get('account_name') for a in info.get('accounts') or []]}

    def cap_signature_send_approved(self, p):
        _, api = self._account()
        env = {'emailSubject': p['title'][:100], 'status': 'sent',
               'documents': [{'documentBase64': base64.b64encode(p['content']).decode(), 'name': p['filename'], 'fileExtension': 'pdf', 'documentId': '1'}],
               'recipients': {'signers': [{'email': s['email'], 'name': '%s %s' % (s['first_name'], s['last_name']), 'recipientId': str(i + 1),
                                           'routingOrder': str(s.get('order', 1)),
                                           'tabs': {'signHereTabs': [{'documentId': '1', 'pageNumber': str(p.get('page', 1)), 'xPosition': str(70 + 160 * (i % 3)),
                                                                      'yPosition': '700'}]}} for i, s in enumerate(p['signers'])]},
               'customFields': {'textCustomFields': [{'name': 'axiorhub_reference', 'value': p['reference'][:100], 'show': 'false'}]}}
        out = self.request('POST', '', env, url=api + '/envelopes')
        return {'remote_ref': out.get('envelopeId', ''), 'etat': out.get('status', 'sent')}

    def cap_signature_status(self, p):
        _, api = self._account()
        out = self.request('GET', '', url=api + '/envelopes/' + quote(p['remote_ref']))
        return {'etat': out.get('status', '')}

    def cap_signature_signed_document(self, p):
        _, api = self._account()
        st = self.cap_signature_status(p)
        if st['etat'] != 'completed':
            raise Stop('signature_non_terminee')
        return {'etat': 'completed', 'fichiers': {
            'document_signe.pdf': self.request('GET', '', url=api + '/envelopes/%s/documents/combined' % quote(p['remote_ref']), raw=True, accept='application/pdf'),
            'certificat.pdf': self.request('GET', '', url=api + '/envelopes/%s/documents/certificate' % quote(p['remote_ref']), raw=True, accept='application/pdf')}}


# ------------------------------------------------------------------------------------------------ I08 SuperPDP
class SuperPDP(ClientCredentialsMixin, Adapter):
    """API bêta : OAuth 2.1 (identifiants client pour les données de l'entreprise). Formats et cycle de vie à qualifier."""
    provider = 'superpdp'

    @property
    def token_url(self):
        return self.base + '/oauth2/token'

    def headers(self):
        return {'Authorization': 'Bearer ' + self.client_token('')}

    def identity(self):
        me = self.request('GET', '/v1.beta/companies/me')
        return {'compte': me.get('name') or me.get('legal_name') or '', 'siren': me.get('siren') or ''}

    def cap_einvoice_receive(self, p):
        out = self.request('GET', '/v1.beta/invoices?' + urlencode({'direction': 'in', 'limit': min(int(p.get('limit', 20)), 50)}))
        return {'factures': (out.get('data') or out.get('items') or out if isinstance(out, list) else [])[:50]}

    def cap_einvoice_receive_detail(self, p):
        """5.6.70 : factures reçues avec leur contenu EN 16931 (numéro, date, vendeur, totaux), les plus récentes d'abord."""
        query = [('direction', 'in'), ('order', 'desc'), ('limit', str(min(int(p.get('limit', 100)), 1000))), ('expand[]', 'en_invoice')]
        out = self.request('GET', '/v1.beta/invoices?' + urlencode(query))
        data = out.get('data') if isinstance(out, dict) else out
        return {'factures': (data or [])[:1000]}

    def cap_ereporting_list(self, p):
        """5.6.70 : e-reportings transmis à l'administration (PPF), vendeur et acheteur, les plus récents d'abord."""
        out = self.request('GET', '/v1.beta/ereportings?' + urlencode({'order': 'desc', 'limit': min(int(p.get('limit', 100)), 1000)}))
        return {'rapports': (out.get('data') if isinstance(out, dict) else out) or []}

    def cap_einvoice_status(self, p):
        out = self.request('GET', '/v1.beta/invoice_events?' + urlencode({'invoice_id': p['remote_ref']}))
        return {'evenements': (out.get('data') if isinstance(out, dict) else out) or []}

    def cap_einvoice_download(self, p):
        return {'fichiers': {'facture': self.request('GET', '/v1.beta/invoices/%s/download' % quote(str(p['remote_ref'])), raw=True, accept='*/*')}}

    def cap_einvoice_send_approved(self, p):
        ref = str(p.get('reference') or '')[:36]   # 5.6.70 : identifiant externe (rapprochement, pas de double dépôt)
        out = self.ctx.http(self.base).request('POST', self.base + '/v1.beta/invoices' + (('?' + urlencode({'external_id': ref})) if ref else ''), p['content'],
                                               {**self.headers(), 'Content-Type': p.get('content_type', 'application/xml'), 'Accept': 'application/json'}, 5_000_000)
        data = json.loads(out or b'{}')
        return {'remote_ref': str(data.get('id') or ''), 'etat': data.get('status') or 'deposee'}


# ------------------------------------------------------------------------------------------------ I09 B2Brouter
class B2Brouter(Adapter):
    provider = 'b2brouter'

    def headers(self):
        h = {'X-B2B-API-Key': self.ctx.secret('api_key')}
        if self.ctx.params.get('api_version'):
            h['X-B2B-API-Version'] = self.ctx.params['api_version']
        return h

    def identity(self):
        out = self.request('GET', '/accounts')
        accounts = out.get('accounts') or []
        wanted = str(self.ctx.params.get('account_id') or '')
        if wanted and not any(str(a.get('identifier') or a.get('id')) == wanted for a in accounts):
            raise Stop('compte_b2brouter_introuvable')
        return {'compte': ', '.join(str(a.get('name') or a.get('identifier')) for a in accounts[:3]), 'comptes': len(accounts)}

    def cap_einvoice_send_approved(self, p):
        acc = self.ctx.params.get('account_id', '')
        if p.get('content'):   # 5.6.70 : facture Factur-X émise par AxiorHub, importée telle quelle puis envoyée par B2Brouter
            hdr = {**self.headers(), 'Content-Type': 'application/octet-stream', 'Accept': 'application/json'}
            target = self.base + '/accounts/%s/invoices/import?%s' % (quote(acc), urlencode({'send_after_import': 'true', 'issued': 'true'}))
            raw = self.ctx.http(target).request('POST', target, p['content'], hdr, 5_000_000)
            inv = (json.loads(raw or b'{}').get('invoice') or json.loads(raw or b'{}'))
            return {'remote_ref': str(inv.get('id') or ''), 'etat': inv.get('state') or 'sending'}
        out = self.request('POST', '/accounts/%s/invoices' % quote(acc), {'invoice': p['invoice'], 'send_after_import': False})
        inv = out.get('invoice') or {}
        self.request('POST', '/invoices/send_invoice/%s' % quote(str(inv.get('id'))), {})
        return {'remote_ref': str(inv.get('id') or ''), 'etat': 'envoyee'}

    def cap_einvoice_status(self, p):
        inv = (self.request('GET', '/invoices/' + quote(str(p['remote_ref']))).get('invoice') or {})
        return {'etat': inv.get('state'), 'numero': inv.get('number')}

    def cap_einvoice_receive(self, p):
        acc = self.ctx.params.get('account_id', '')
        out = self.request('GET', '/accounts/%s/invoices?%s' % (quote(acc), urlencode({'type': 'ReceivedInvoice', 'limit': min(int(p.get('limit', 20)), 50)})))
        return {'factures': [{'id': i.get('id'), 'numero': i.get('number'), 'date': i.get('date'), 'etat': i.get('state')} for i in out.get('invoices') or []]}

    def cap_einvoice_receive_detail(self, p):
        """5.6.70 : factures reçues (fournisseur, montants, état) — liste réduite de l'API."""
        acc = self.ctx.params.get('account_id', '')
        out = self.request('GET', '/accounts/%s/invoices?%s' % (quote(acc), urlencode({'type': 'ReceivedInvoice', 'limit': min(int(p.get('limit', 100)), 500)})))
        return {'factures': out.get('invoices') or []}

    def cap_ereporting_list(self, p):
        """5.6.70 : comptes rendus fiscaux (tax reports) transmis par B2Brouter."""
        acc = self.ctx.params.get('account_id', '')
        out = self.request('GET', '/accounts/%s/tax_reports?%s' % (quote(acc), urlencode({'limit': min(int(p.get('limit', 100)), 500)})))
        return {'rapports': out.get('tax_reports') or []}

    def cap_einvoice_download(self, p):
        return {'fichiers': {'facture.pdf': self.request('GET', '/invoices/%s/as/pdf.invoice' % quote(str(p['remote_ref'])), raw=True, accept='*/*')}}


# ------------------------------------------------------------------------------------------------ I10 GoCardless
class GoCardless(Adapter):
    provider = 'gocardless'

    def headers(self):
        return {'Authorization': 'Bearer ' + self.ctx.secret('access_token'), 'GoCardless-Version': '2015-07-06'}

    def identity(self):
        out = self.request('GET', '/creditors?limit=1')
        cred = (out.get('creditors') or [{}])[0]
        return {'compte': cred.get('name', ''), 'creancier': cred.get('id', '')}

    def cap_payment_prepare_mandate(self, p):
        br = self.request('POST', '/billing_requests', {'billing_requests': {'mandate_request': {'scheme': 'sepa_core'},
                                                                              'metadata': {'axiorhub': p['reference'][:40]}}},
                          headers={'Idempotency-Key': p['idempotency_key']})['billing_requests']
        flow = self.request('POST', '/billing_request_flows', {'billing_request_flows': {'links': {'billing_request': br['id']}}},
                            headers={'Idempotency-Key': p['idempotency_key'] + '-flux'})['billing_request_flows']
        return {'remote_ref': br['id'], 'etat': br.get('status'), 'lien_autorisation': flow.get('authorisation_url')}

    def cap_payment_submit_authorized(self, p):
        pay = self.request('POST', '/payments', {'payments': {'amount': int(p['amount_cents']), 'currency': 'EUR', 'description': p['description'][:100],
                                                             'links': {'mandate': p['mandate']}, 'metadata': {'axiorhub': p['reference'][:40]}}},
                           headers={'Idempotency-Key': p['idempotency_key']})['payments']
        return {'remote_ref': pay['id'], 'etat': pay.get('status')}

    def cap_payment_status(self, p):
        ref = str(p['remote_ref'])
        if ref.startswith('BRQ'):
            br = self.request('GET', '/billing_requests/' + quote(ref))['billing_requests']
            return {'etat': br.get('status'), 'mandat': (br.get('links') or {}).get('mandate_request_mandate', '')}
        pay = self.request('GET', '/payments/' + quote(ref))['payments']
        return {'etat': pay.get('status'), 'montant': pay.get('amount')}


# ------------------------------------------------------------------------------------------------ I11 Chorus Pro
class ChorusPro(PisteLegal):
    """Raccordement PISTE + compte technique (en-tête ``cpro-account``). Mode de raccordement et charges utiles à qualifier."""
    provider = 'chorus_pro'

    def headers(self):
        login = '%s:%s' % (self.ctx.params.get('technical_login', ''), self.ctx.secret('technical_password'))
        return {'Authorization': 'Bearer ' + self.client_token('openid'), 'cpro-account': base64.b64encode(login.encode()).decode()}

    def identity(self):
        out = self.request('GET', '/cpro/transverses/v1/health-check')
        return {'compte': self.ctx.params.get('technical_login', ''), 'service': str(out.get('codeRetour', out.get('status', 'disponible')))}

    def cap_public_invoice_prepare(self, p):
        out = self.request('POST', '/cpro/structures/v1/rechercher', {'structure': {'identifiantStructure': p['siret'], 'typeIdentifiantStructure': 'SIRET'}})
        return {'structures': out.get('listeStructures') or [], 'code_retour': out.get('codeRetour')}

    def cap_public_invoice_submit_approved(self, p):
        out = self.request('POST', '/cpro/factures/v1/soumettre', p['facture'])
        return {'remote_ref': str(out.get('identifiantFactureCPP') or ''), 'etat': out.get('statutFacture') or out.get('libelle', ''),
                'code_retour': out.get('codeRetour')}

    def cap_public_invoice_status(self, p):
        out = self.request('POST', '/cpro/factures/v1/consulter/fournisseur', {'identifiantFactureCPP': int(p['remote_ref'])})
        return {'etat': out.get('statutFacture') or out.get('libelleStatut', ''), 'code_retour': out.get('codeRetour')}


# ------------------------------------------------------------------------------------------------ I12 Maileva
class Maileva(Adapter):
    """Courrier papier (Mail v2) : jeton par identifiants du contrat ; options, coût et preuves selon l'offre — à qualifier."""
    provider = 'maileva'
    _cache = {}

    def _token(self):
        key = (self.ctx.env, self.ctx.params.get('client_id', ''), self.ctx.params.get('username', ''))
        cached = self._cache.get(key)
        if cached and cached[1] > time.time() + 30:
            return cached[0]
        url = url_of('maileva_auth', self.ctx.env)
        form = {'grant_type': 'password', 'client_id': self.ctx.params.get('client_id', ''), 'client_secret': self.ctx.secret('client_secret'),
                'username': self.ctx.params.get('username', ''), 'password': self.ctx.secret('password')}
        out = json.loads(self.ctx.http(url).request('POST', url, urlencode(form).encode(), {'Content-Type': 'application/x-www-form-urlencoded'}, 500_000))
        if not out.get('access_token'):
            raise Stop('jeton_fournisseur_invalide')
        self._cache[key] = (out['access_token'], time.time() + int(out.get('expires_in', 600)))
        return out['access_token']

    def headers(self):
        return {'Authorization': 'Bearer ' + self._token()}

    def identity(self):
        out = self.request('GET', '/sendings?start_index=1&count=1')
        return {'compte': self.ctx.params.get('username', ''), 'envois_visibles': out.get('count', len(out.get('sendings') or []))}

    def cap_paper_mail_send_approved(self, p):
        s = self.request('POST', '/sendings', {'name': p['title'][:100]})
        sid = s['id']
        self.request('POST', '/sendings/%s/documents' % sid, multipart=({'metadata': json.dumps({'priority': 1, 'name': p['filename']})},
                                                                         {'document': (p['filename'], p['content'], 'application/pdf')}))
        self.request('POST', '/sendings/%s/recipients' % sid, p['address'])
        self.request('PATCH', '/sendings/%s' % sid, {'postage_type': p.get('postage_type', 'ECONOMIC'), 'color_printing': bool(p.get('color')),
                                                    'duplex_printing': bool(p.get('duplex', True))})
        self.request('POST', '/sendings/%s/submit' % sid, {})
        return {'remote_ref': sid, 'etat': 'soumis'}

    def cap_paper_mail_status(self, p):
        out = self.request('GET', '/sendings/' + quote(str(p['remote_ref'])))
        return {'etat': out.get('status'), 'cout': out.get('price') or out.get('billed_price')}

    def cap_paper_mail_proofs(self, p):
        return {'fichiers': {'preuve_de_depot.pdf': self.request('GET', '/sendings/%s/download_deposit_proof' % quote(str(p['remote_ref'])), raw=True,
                                                                 accept='application/pdf')}}


# ------------------------------------------------------------------------------------------------ I13 Microsoft Graph
GRAPH_SCOPES = {'calendar.read': 'Calendars.Read', 'mail.read': 'Mail.Read', 'mail.create_draft': 'Mail.ReadWrite', 'contacts.read': 'Contacts.Read',
                'documents.read_selected': 'Files.Read'}


class MicrosoftGraph(OAuthCodeMixin, Adapter):
    provider = 'microsoft_graph'

    @property
    def token_url(self):
        return '%s/%s/oauth2/v2.0/token' % (url_of('microsoft_login', self.ctx.env), quote(self.ctx.params.get('tenant') or 'common'))

    @property
    def authorize_url(self):
        return '%s/%s/oauth2/v2.0/authorize' % (url_of('microsoft_login', self.ctx.env), quote(self.ctx.params.get('tenant') or 'common'))

    @staticmethod
    def scopes(capabilities):
        return ' '.join(['offline_access', 'User.Read'] + sorted({GRAPH_SCOPES[c] for c in capabilities if c in GRAPH_SCOPES}))

    def identity(self):
        me = self.request('GET', '/me?$select=userPrincipalName,displayName')
        return {'compte': me.get('userPrincipalName') or me.get('displayName') or ''}

    def cap_calendar_read(self, p):
        start = p.get('start') or datetime.now(timezone.utc).isoformat()
        end = p.get('end') or (datetime.now(timezone.utc) + timedelta(days=14)).isoformat()
        out = self.request('GET', '/me/calendarview?' + urlencode({'startDateTime': start, 'endDateTime': end, '$top': 50, '$select': 'subject,start,end,location'}))
        return {'evenements': [{'titre': x.get('subject'), 'debut': (x.get('start') or {}).get('dateTime'), 'fin': (x.get('end') or {}).get('dateTime')}
                               for x in out.get('value') or []]}

    def cap_mail_read(self, p):
        out = self.request('GET', '/me/messages?' + urlencode({'$top': min(int(p.get('limit', 10)), 25), '$select': 'subject,from,receivedDateTime,bodyPreview,internetMessageId'}))
        return {'messages': [{'objet': m.get('subject'), 'de': ((m.get('from') or {}).get('emailAddress') or {}).get('address'), 'date': m.get('receivedDateTime'),
                              'apercu': m.get('bodyPreview'), 'message_id': m.get('internetMessageId')} for m in out.get('value') or []]}

    def cap_mail_create_draft(self, p):
        out = self.request('POST', '/me/messages', {'subject': p['subject'][:200], 'body': {'contentType': 'Text', 'content': p['body'][:50000]},
                                                    'toRecipients': [{'emailAddress': {'address': a}} for a in p.get('to', [])[:20]]})
        return {'remote_ref': out.get('id', ''), 'etat': 'brouillon'}

    def cap_contacts_read(self, p):
        out = self.request('GET', '/me/contacts?' + urlencode({'$top': 25, '$select': 'displayName,emailAddresses,companyName'}))
        return {'contacts': [{'nom': c.get('displayName'), 'societe': c.get('companyName')} for c in out.get('value') or []]}

    def cap_documents_read_selected(self, p):
        folder = str(self.ctx.params.get('drive_folder') or '').strip('/')
        if not folder:
            raise Stop('dossier_onedrive_non_choisi')
        out = self.request('GET', '/me/drive/root:/%s:/children?$top=50' % quote(folder))
        return {'fichiers': [{'id': f.get('id'), 'nom': f.get('name'), 'modifie': f.get('lastModifiedDateTime'), 'version': f.get('eTag')} for f in out.get('value') or []]}


# ------------------------------------------------------------------------------------------------ I14 Google Workspace
GOOGLE_SCOPES = {'calendar.read': 'https://www.googleapis.com/auth/calendar.readonly', 'mail.read': 'https://www.googleapis.com/auth/gmail.readonly',
                 'mail.create_draft': 'https://www.googleapis.com/auth/gmail.compose', 'documents.read_selected': 'https://www.googleapis.com/auth/drive.readonly',
                 'documents.export': 'https://www.googleapis.com/auth/drive.readonly'}


class GoogleWorkspace(OAuthCodeMixin, Adapter):
    provider = 'google_workspace'
    token_url = 'https://oauth2.googleapis.com/token'
    authorize_url = 'https://accounts.google.com/o/oauth2/v2/auth'

    @staticmethod
    def scopes(capabilities):
        return ' '.join(['openid', 'email'] + sorted({GOOGLE_SCOPES[c] for c in capabilities if c in GOOGLE_SCOPES}))

    def identity(self):
        me = self.request('GET', '', url='https://openidconnect.googleapis.com/v1/userinfo')
        return {'compte': me.get('email', '')}

    def cap_calendar_read(self, p):
        cal = quote(self.ctx.params.get('calendar_id') or 'primary')
        out = self.request('GET', '/calendar/v3/calendars/%s/events?%s' % (cal, urlencode({'timeMin': datetime.now(timezone.utc).isoformat(), 'maxResults': 50,
                                                                                              'singleEvents': 'true', 'orderBy': 'startTime'})))
        return {'evenements': [{'titre': x.get('summary'), 'debut': (x.get('start') or {}).get('dateTime') or (x.get('start') or {}).get('date')}
                               for x in out.get('items') or []]}

    def cap_mail_read(self, p):
        out = self.request('GET', '', url='https://gmail.googleapis.com/gmail/v1/users/me/messages?' + urlencode({'maxResults': min(int(p.get('limit', 10)), 25)}))
        return {'messages': [{'id': m.get('id'), 'fil': m.get('threadId')} for m in out.get('messages') or []]}

    def cap_mail_create_draft(self, p):
        from email.message import EmailMessage
        msg = EmailMessage()
        msg['To'] = ', '.join(p.get('to', [])[:20])
        msg['Subject'] = p['subject'][:200]
        msg.set_content(p['body'][:50000])
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        out = self.request('POST', '', {'message': {'raw': raw}}, url='https://gmail.googleapis.com/gmail/v1/users/me/drafts')
        return {'remote_ref': out.get('id', ''), 'etat': 'brouillon'}

    def cap_documents_read_selected(self, p):
        folder = str(self.ctx.params.get('drive_folder_id') or '')
        if not folder:
            raise Stop('dossier_drive_non_choisi')
        out = self.request('GET', '/drive/v3/files?' + urlencode({'q': "'%s' in parents and trashed=false" % folder.replace("'", ''), 'pageSize': 50,
                                                                 'fields': 'files(id,name,mimeType,modifiedTime,version)'}))
        return {'fichiers': out.get('files') or []}

    def cap_documents_export(self, p):
        fid = quote(str(p['id']))
        meta = self.request('GET', '/drive/v3/files/%s?fields=id,name,mimeType,version' % fid)
        if str(meta.get('mimeType', '')).startswith('application/vnd.google-apps.'):
            data = self.request('GET', '/drive/v3/files/%s/export?mimeType=application/pdf' % fid, raw=True, accept='*/*')
            name = meta.get('name', 'document') + '.pdf'
        else:
            data = self.request('GET', '/drive/v3/files/%s?alt=media' % fid, raw=True, accept='*/*')
            name = meta.get('name', 'document')
        return {'fichiers': {name: data}, 'version': meta.get('version'), 'id': meta.get('id')}


# ------------------------------------------------------------------------------------------------ I15 Axonaut
class Axonaut(Adapter):
    provider = 'axonaut'

    def headers(self):
        return {'userApiKey': self.ctx.secret('api_key')}

    def identity(self):
        me = self.request('GET', '/me')
        return {'compte': me.get('email') or me.get('name') or '', 'societe': (me.get('company') or {}).get('name', '') if isinstance(me.get('company'), dict) else ''}

    def cap_crm_lookup(self, p):
        q = {'siret': p['siret']} if p.get('siret') else {'search': p['query'][:120]}
        out = self.request('GET', '/companies?' + urlencode(q))
        items = out if isinstance(out, list) else out.get('companies') or []
        return {'societes': [{'id': c.get('id'), 'nom': c.get('name'), 'siret': c.get('siret'), 'client': c.get('is_customer')} for c in items[:20]]}

    def cap_billing_prepare_when_supported(self, p):
        out = self.request('POST', '/quotations', p['quotation'])
        return {'remote_ref': str(out.get('id') or ''), 'etat': 'devis_brouillon'}


# ------------------------------------------------------------------------------------------------ données publiques (sans clé)
class RechercheEntreprises(Adapter):
    """API Recherche d'entreprises (données publiques, sans authentification) : premier rapprochement d'un SIREN."""
    provider = 'recherche_entreprises'

    def identity(self):
        return {'compte': 'service public sans authentification'}

    def cap_company_lookup(self, p):
        q = str(p.get('siren') or p.get('query') or '').strip()
        out = self.request('GET', '/search?' + urlencode({'q': q[:120], 'page': 1, 'per_page': 5}))
        res = []
        for r in out.get('results') or []:
            siege = r.get('siege') or {}
            voie = ' '.join(str(siege.get(k)) for k in ('numero_voie', 'indice_repetition', 'type_voie', 'libelle_voie') if siege.get(k))
            res.append({'siren': r.get('siren'), 'nom': r.get('nom_complet') or r.get('nom_raison_sociale'), 'siege': siege.get('adresse'),
                        'siret': siege.get('siret'), 'adresse_ligne1': voie or None, 'code_postal': siege.get('code_postal'),
                        'ville': siege.get('libelle_commune'), 'etat': r.get('etat_administratif'), 'dirigeants': [d.get('nom') or d.get('denomination') for d in (r.get('dirigeants') or [])][:5]})
        return {'resultats': res, 'source': 'API Recherche d’entreprises (data.gouv.fr)', 'date': datetime.now(timezone.utc).date().isoformat(),
                'limite': 'Données d’annuaire : elles ne prouvent pas le pouvoir de signer de la personne qui engage le client.'}


ADAPTERS = {'ar24': AR24, 'youtrust': Youtrust, 'legifrance_piste': Legifrance, 'judilibre_piste': Judilibre, 'pappers_justice': PappersJustice,
            'pennylane': Pennylane, 'docusign': DocuSign, 'superpdp': SuperPDP, 'b2brouter': B2Brouter, 'gocardless': GoCardless,
            'chorus_pro': ChorusPro, 'maileva': Maileva, 'microsoft_graph': MicrosoftGraph, 'google_workspace': GoogleWorkspace, 'axonaut': Axonaut,
            'recherche_entreprises': RechercheEntreprises}
