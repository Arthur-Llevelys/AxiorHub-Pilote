"""5.6.112 : réglage automatique de l'Open WebUI du cabinet d'après les réglages d'AxiorHub Pilote (instance neuve ou existante).

Appelé par ``configure-openwebui.py`` (root, sur le serveur : seul root lit le jeton de l'outil) avec une clé d'API d'un ADMINISTRATEUR
d'Open WebUI saisie à l'invite (jamais enregistrée, jamais affichée). Chaque étape relit l'état d'Open WebUI puis écrit seulement ce qui
manque ou a changé (relancer ne crée aucun doublon) ; rien de ce que le cabinet a réglé lui-même n'est retiré.

Étapes (formes essayées sur Open WebUI 0.13.5, ``ghcr.io/open-webui/open-webui@sha256:e0dc5a8b…``) :
- ``outil`` : outil AxiorHub (``integrations/openwebui/axiorhub_tool.py``) créé ou mis à jour (``/api/v1/tools/create`` ou
  ``/id/axiorhub/update``), ses réglages (adresse de l'API AxiorHub, jeton) posés (``/valves/update``), ouvert à tout le cabinet ;
- ``connexions`` : connexions compatibles OpenAI (Mistral, OpenRouter, OpenAI, Anthropic, Gemini) et Ollama d'AxiorHub ajoutées
  (``/openai/config/update``, ``/ollama/config/update``) ; une clé de fournisseur n'est transmise que si la personne l'a accepté ;
- ``mcp`` : connecteurs MCP actifs du registre d'AxiorHub ajoutés aux serveurs d'outils (``/api/v1/configs/tool_servers``, type
  ``mcp``) ; leur jeton suit la même règle que les clés ;
- ``competences`` : compétences approuvées par un avocat dans AxiorHub (texte déclaratif seulement) copiées (``/api/v1/skills``) ;
- ``modele`` : modèle « AxiorHub Avocat » (invite système ``SYSTEM-PROMPT-AXIORHUB.md``, outil et compétences AxiorHub) sur un modèle de
  base réellement proposé par Open WebUI, ouvert à tout le cabinet et choisi par défaut (``/api/v1/configs/models``) ;
- ``invites`` : invites ``/dossier``, ``/echeances``, ``/courriel``, ``/recherche`` (``/api/v1/prompts``) ;
- ``permissions`` : clés d'API permises aux comptes (``/api/v1/users/default/permissions``) ;
- ``parametres`` : chaque modèle employé par AxiorHub et proposé par Open WebUI reçoit les paramètres de génération d'AxiorHub (fonction
  « assistant », profil Juridique par défaut : température, top_p, top_k, min_p, pénalité de répétition, contexte, sans « réflexion »),
  filtrés comme AxiorHub les transmet au fournisseur ; le modèle « AxiorHub Avocat » reçoit les mêmes ;
- ``voix`` : synthèse (Kokoro ou Chatterbox : même serveur, même voix, même vitesse ; ElevenLabs si sa clé est acceptée ; Brindille et
  eSpeak, propres à AxiorHub : voix du navigateur) et dictée (Whisper de même taille que la dictée d'AxiorHub) (``/api/v1/audio/config``).
Accès « tout le cabinet » : ``{'principal_type': 'user', 'principal_id': '*', 'permission': 'read'}`` (lecture seule).
"""
import json
import re
from urllib.parse import quote, urlsplit

OUTIL_ID = 'axiorhub'
MODELE_ID = 'axiorhub-avocat'
PUBLIC = [{'principal_type': 'user', 'principal_id': '*', 'permission': 'read'}]
COMPATIBLES = {'openai': 'https://api.openai.com/v1', 'mistral': 'https://api.mistral.ai/v1', 'openrouter': 'https://openrouter.ai/api/v1',
               'anthropic': 'https://api.anthropic.com/v1', 'gemini': 'https://generativelanguage.googleapis.com/v1beta/openai'}
INVITES = (
    ('dossier', 'Point sur un dossier', 'Avec l’outil AxiorHub, fais le point sur le dossier {{dossier}} : faits, procédure, échéances, pièces, '
     'dernières correspondances et prochaines actions. Cite les pièces et les dates exactes ; signale ce qui manque.'),
    ('echeances', 'Mes échéances', 'Avec l’outil AxiorHub, liste mes échéances et audiences des {{jours}} prochains jours, dossier par dossier, '
     'de la plus proche à la plus lointaine, avec ce qui reste à préparer.'),
    ('courriel', 'Projet de réponse à un courriel', 'Avec l’outil AxiorHub, prépare un projet de réponse au dernier courriel du dossier {{dossier}}. '
     'Ne l’envoie pas : propose-le à la relecture dans AxiorHub et rappelle les points à vérifier.'),
    ('recherche', 'Recherche juridique', 'Recherche juridique : {{question}}. Donne la règle, la jurisprudence pertinente et les sources exactes '
     '(textes, décisions, dates) ; distingue ce qui est certain de ce qui reste à vérifier.'),
)
ETATS = ('fait', 'a_jour', 'saute', 'echec')


class ErreurOpenWebUI(Exception):
    pass


class Client:
    """Client de l'API d'Open WebUI : HTTPS (certificat vérifié) ; HTTP seulement pour une adresse de bouclage (instance de ce serveur)."""

    def __init__(self, base, cle, delai=60, transport=None):
        p = urlsplit(str(base or '').strip())
        boucle = (p.hostname or '') in ('127.0.0.1', 'localhost', '::1')
        if not p.hostname or p.username or p.password or p.query or p.fragment or not (p.scheme == 'https' or (p.scheme == 'http' and boucle)):
            raise ErreurOpenWebUI('adresse_openwebui_invalide')
        if not re.fullmatch(r'[A-Za-z0-9._\-]{8,512}', str(cle or '')):
            raise ErreurOpenWebUI('cle_admin_invalide')
        self.p, self.cle, self.delai, self.transport = p, cle, delai, transport

    def appel(self, methode, chemin, corps=None):
        if self.transport is not None:   # essais : faux Open WebUI
            return self.transport(methode, chemin, corps)
        import http.client
        import ssl
        conn = (http.client.HTTPSConnection(self.p.hostname, self.p.port or 443, timeout=self.delai, context=ssl.create_default_context())
                if self.p.scheme == 'https' else http.client.HTTPConnection(self.p.hostname, self.p.port or 80, timeout=self.delai))
        entetes = {'Authorization': 'Bearer ' + self.cle, 'Accept': 'application/json', 'User-Agent': 'AxiorHub-Pilote'}
        donnees = None
        if corps is not None:
            donnees = json.dumps(corps, ensure_ascii=False).encode('utf-8')
            entetes['Content-Type'] = 'application/json'
        try:
            conn.request(methode, self.p.path.rstrip('/') + chemin, body=donnees, headers=entetes)
            r = conn.getresponse()
            brut, statut = r.read(20_000_000), r.status
        except (OSError, http.client.HTTPException, ssl.SSLError):
            raise ErreurOpenWebUI('openwebui_injoignable') from None
        finally:
            conn.close()
        try:
            return statut, (json.loads(brut.decode('utf-8')) if brut else None)
        except ValueError:
            return statut, None

    def exiger(self, methode, chemin, corps=None):
        statut, data = self.appel(methode, chemin, corps)
        if statut in (401, 403):
            raise ErreurOpenWebUI('cle_admin_refusee')
        if statut >= 400:
            raise ErreurOpenWebUI('http_%d sur %s' % (statut, chemin.split('?')[0]))
        return data


def verifier_admin(client):
    """La clé est celle d'un administrateur : la lecture des connexions est réservée aux administrateurs."""
    data = client.exiger('GET', '/openai/config')
    if not isinstance(data, dict) or 'OPENAI_API_BASE_URLS' not in data:
        raise ErreurOpenWebUI('reponse_inattendue')
    return True


def _resultat(etape, etat, detail):
    return {'etape': etape, 'etat': etat, 'detail': detail}


# ------------------------------------------------------------------------------------------------ outil AxiorHub
def outil_existant(client):
    """Identifiant de l'outil AxiorHub déjà présent : ``axiorhub``, sinon une copie importée à la main sous un autre identifiant (nom ou
    identifiant contenant « axiorhub » et source portant ``AXIORHUB_API_TOKEN``) ; ``''`` s'il n'y en a pas."""
    statut, actuel = client.appel('GET', '/api/v1/tools/id/' + OUTIL_ID)
    if statut == 200 and isinstance(actuel, dict):
        return OUTIL_ID
    for item in client.exiger('GET', '/api/v1/tools/') or []:
        if not isinstance(item, dict):
            continue
        ident = str(item.get('id') or '')
        if 'axiorhub' not in (ident + ' ' + str(item.get('name') or '')).lower() or not re.fullmatch(r'[A-Za-z0-9_]{1,100}', ident):
            continue
        statut, detail = client.appel('GET', '/api/v1/tools/id/' + ident)
        if statut == 200 and isinstance(detail, dict) and 'AXIORHUB_API_TOKEN' in str(detail.get('content') or ''):
            return ident
    return ''


def outil(client, source, api_url, jeton):
    ident = outil_existant(client)
    if ident:
        actuel = client.exiger('GET', '/api/v1/tools/id/' + ident) or {}
        forme = {'id': ident, 'name': actuel.get('name') or 'AxiorHub Pilote', 'content': source,
                 'meta': actuel.get('meta') or {'description': 'Dossiers, courriels, agenda et documents du cabinet (AxiorHub Pilote)', 'manifest': {}}}
        meme = actuel.get('content') == source
        if not meme:
            client.exiger('POST', '/api/v1/tools/id/%s/update' % ident, forme)
        cree = False
    else:
        ident = OUTIL_ID
        client.exiger('POST', '/api/v1/tools/create', {'id': OUTIL_ID, 'name': 'AxiorHub Pilote', 'content': source,
                                                        'meta': {'description': 'Dossiers, courriels, agenda et documents du cabinet (AxiorHub Pilote)',
                                                                 'manifest': {}}})
        meme, cree = False, True
    valves = client.exiger('GET', '/api/v1/tools/id/%s/valves' % ident) or {}
    voulues = dict(valves) if isinstance(valves, dict) else {}
    voulues.update({'AXIORHUB_API_URL': api_url, 'AXIORHUB_API_TOKEN': jeton})
    if voulues != valves:
        client.exiger('POST', '/api/v1/tools/id/%s/valves/update' % ident, voulues)
    client.exiger('POST', '/api/v1/tools/id/%s/access/update' % ident, {'access_grants': PUBLIC})
    nom = '' if ident == OUTIL_ID else ' (copie déjà importée « %s », mise à jour sans doublon)' % ident
    if cree:
        return _resultat('outil', 'fait', 'Outil AxiorHub installé, relié à %s, ouvert à tout le cabinet.' % api_url)
    if meme and voulues == valves:
        return _resultat('outil', 'a_jour', 'Outil AxiorHub déjà à jour%s.' % nom)
    return _resultat('outil', 'fait', 'Outil AxiorHub mis à jour (version de ce serveur, réglages d’accès à AxiorHub)%s.' % nom)


# ------------------------------------------------------------------------------------------------ connexions IA
def fournisseurs_compatibles(registre):
    """Connexions d'AxiorHub reprises par Open WebUI : (id, type, adresse, modèle, fichier de clé). ChatGPT (abonnement) et Ollama à part."""
    out = []
    for pid, cfg in sorted((registre or {}).items()):
        if not isinstance(cfg, dict) or cfg.get('type') not in COMPATIBLES or cfg.get('enabled') is False:
            continue
        url = str(cfg.get('url') or '').strip().rstrip('/') or COMPATIBLES[cfg['type']]
        if cfg['type'] == 'gemini':
            url = COMPATIBLES['gemini']
        out.append({'id': pid, 'type': cfg['type'], 'url': url, 'modele': str(cfg.get('model') or ''), 'secret_file': str(cfg.get('secret_file') or '')})
    return out


def connexions(client, fournisseurs, cles, ollama_url=''):
    """``cles`` : {id: clé} pour les seuls fournisseurs dont la personne a accepté la transmission ; sans clé, la connexion est ajoutée
    désactivée (à compléter dans Open WebUI)."""
    faites = []
    config = client.exiger('GET', '/openai/config') or {}
    urls = list(config.get('OPENAI_API_BASE_URLS') or [])
    keys = list(config.get('OPENAI_API_KEYS') or [])
    confs = dict(config.get('OPENAI_API_CONFIGS') or {})
    while len(keys) < len(urls):
        keys.append('')
    change = False
    for f in fournisseurs:
        cle = cles.get(f['id'], '')
        if f['url'] in urls:
            i = urls.index(f['url'])
            if cle and keys[i] != cle:
                keys[i] = cle
                confs.setdefault(str(i), {})['enable'] = True
                change = True
                faites.append('%s : clé mise à jour' % f['id'])
            continue
        urls.append(f['url'])
        keys.append(cle)
        confs[str(len(urls) - 1)] = {'enable': bool(cle), 'tags': [], 'prefix_id': '', 'model_ids': [f['modele']] if f['modele'] else [],
                                     'connection_type': 'external'}
        change = True
        faites.append('%s : %s' % (f['id'], 'ajoutée' if cle else 'ajoutée désactivée, clé à saisir dans Open WebUI'))
    if change:
        client.exiger('POST', '/openai/config/update', {'ENABLE_OPENAI_API': True if any(confs.get(str(i), {}).get('enable', True) and keys[i]
                                                                                        for i in range(len(urls))) else bool(config.get('ENABLE_OPENAI_API')),
                                                        'OPENAI_API_BASE_URLS': urls, 'OPENAI_API_KEYS': keys, 'OPENAI_API_CONFIGS': confs})
    if ollama_url:
        oc = client.exiger('GET', '/ollama/config') or {}
        ourls = list(oc.get('OLLAMA_BASE_URLS') or [])
        if ollama_url not in ourls or not oc.get('ENABLE_OLLAMA_API'):
            if ollama_url not in ourls:
                ourls.append(ollama_url)
            client.exiger('POST', '/ollama/config/update', {'ENABLE_OLLAMA_API': True, 'OLLAMA_BASE_URLS': ourls,
                                                             'OLLAMA_API_CONFIGS': dict(oc.get('OLLAMA_API_CONFIGS') or {})})
            faites.append('Ollama (%s) : ajouté' % ollama_url)
    if not faites:
        return _resultat('connexions', 'a_jour', 'Connexions IA déjà présentes.')
    return _resultat('connexions', 'fait', 'Connexions IA : ' + ' ; '.join(faites) + '.')


def registre_fournisseurs(config):
    from .ai_gateway import provider_registry
    return provider_registry(config)


# ------------------------------------------------------------------------------------------------ connecteurs MCP
def connecteurs_mcp(extensions):
    out, ecartes = [], []
    for item in (extensions or {}).values():
        if not isinstance(item, dict) or item.get('kind') != 'connector' or not item.get('enabled') or not item.get('endpoint'):
            continue
        if item.get('auth_type') not in ('none', 'bearer', None, ''):
            ecartes.append(str(item.get('name') or item.get('id')))   # OAuth : à relier dans Open WebUI (l'autorisation est personnelle)
            continue
        out.append({'id': str(item.get('id') or ''), 'nom': str(item.get('name') or item.get('id') or ''), 'url': str(item['endpoint']),
                    'auth': 'bearer' if item.get('auth_type') == 'bearer' else 'none', 'secret_file': str(item.get('secret_file') or '')})
    return out, ecartes


def mcp(client, connecteurs, cles, ecartes=()):
    data = client.exiger('GET', '/api/v1/configs/tool_servers') or {}
    liste = list(data.get('TOOL_SERVER_CONNECTIONS') or [])
    deja = {str(x.get('url')) for x in liste if isinstance(x, dict)}
    ajouts = []
    for c in connecteurs:
        if c['url'] in deja:
            continue
        cle = cles.get(c['id'], '') if c['auth'] == 'bearer' else ''
        liste.append({'url': c['url'], 'path': '', 'type': 'mcp', 'auth_type': 'bearer' if cle else 'none', 'key': cle,
                      'config': {'enable': c['auth'] == 'none' or bool(cle)}, 'info': {'id': 'axiorhub-' + re.sub(r'[^a-z0-9-]', '-', c['id'].lower())[:40],
                                                                                    'name': c['nom'], 'description': 'Connecteur MCP repris d’AxiorHub Pilote'}})
        ajouts.append(c['nom'] + ('' if (c['auth'] == 'none' or cle) else ' (désactivé : jeton à saisir dans Open WebUI)'))
    if ajouts:
        client.exiger('POST', '/api/v1/configs/tool_servers', {'TOOL_SERVER_CONNECTIONS': liste})
    note = (' Connecteurs OAuth à relier dans Open WebUI : %s.' % ', '.join(ecartes)) if ecartes else ''
    if not ajouts:
        return _resultat('mcp', 'a_jour' if connecteurs else 'saute', ('Connecteurs MCP déjà présents.' if connecteurs else 'Aucun connecteur MCP actif dans AxiorHub.') + note)
    return _resultat('mcp', 'fait', 'Connecteurs MCP ajoutés : ' + ', '.join(ajouts) + '.' + note)


# ------------------------------------------------------------------------------------------------ compétences
def competences_axiorhub(extensions):
    out = []
    for item in (extensions or {}).values():
        if (isinstance(item, dict) and item.get('kind') in ('skill', 'plugin') and item.get('enabled') and item.get('reviewed')
                and str(item.get('skill_text') or '').strip()):
            ident = 'axiorhub-' + re.sub(r'[^a-z0-9_-]', '-', str(item.get('id') or '').lower())[:60]
            out.append({'id': ident, 'nom': str(item.get('name') or item.get('id')), 'texte': str(item['skill_text'])[:80_000]})
    return out


def competences(client, liste):
    faites, ids = [], []
    for c in liste:
        forme = {'id': c['id'], 'name': c['nom'], 'description': 'Compétence approuvée dans AxiorHub Pilote', 'content': c['texte']}
        statut, actuel = client.appel('GET', '/api/v1/skills/id/' + quote(c['id'], safe=''))
        if statut == 200 and isinstance(actuel, dict):
            if actuel.get('content') != c['texte'] or actuel.get('name') != c['nom']:
                client.exiger('POST', '/api/v1/skills/id/%s/update' % quote(c['id'], safe=''), forme)
                faites.append(c['nom'] + ' (mise à jour)')
        else:
            client.exiger('POST', '/api/v1/skills/create', forme)
            faites.append(c['nom'])
        client.exiger('POST', '/api/v1/skills/id/%s/access/update' % quote(c['id'], safe=''), {'access_grants': PUBLIC})
        ids.append(c['id'])
    if not liste:
        return _resultat('competences', 'saute', 'Aucune compétence approuvée dans AxiorHub.'), ids
    if not faites:
        return _resultat('competences', 'a_jour', 'Compétences déjà à jour.'), ids
    return _resultat('competences', 'fait', 'Compétences copiées : ' + ', '.join(faites) + '.'), ids


# ------------------------------------------------------------------------------------------------ modèle AxiorHub Avocat
def modeles_disponibles(client):
    data = client.exiger('GET', '/api/models') or {}
    return [str(m.get('id')) for m in (data.get('data') or []) if isinstance(m, dict) and m.get('id') and m.get('id') != MODELE_ID]


def proprietaires(client):
    """{modèle: « ollama » ou « openai »} d'après Open WebUI (``owned_by``)."""
    data = client.exiger('GET', '/api/models') or {}
    return {str(m.get('id')): str(m.get('owned_by') or '') for m in (data.get('data') or []) if isinstance(m, dict) and m.get('id')}


def choisir_base(disponibles, prefere=''):
    if prefere and prefere in disponibles:
        return prefere
    return disponibles[0] if disponibles else ''


def modele(client, systeme, base, competences_ids=(), defaut=True, params=None, outil_id=OUTIL_ID):
    """``params`` : paramètres de génération d'AxiorHub pour ce modèle de base (``parametres_axiorhub``), posés avec l'invite système."""
    if not base:
        return _resultat('modele', 'saute', 'Aucun modèle proposé par Open WebUI : ajoutez une connexion IA, puis relancez ce réglage.')
    voulus = dict(params or {})
    voulus['system'] = systeme
    forme = {'id': MODELE_ID, 'base_model_id': base, 'name': 'AxiorHub Avocat',
             'meta': {'description': 'Assistant du cabinet relié à AxiorHub Pilote (dossiers, courriels, agenda)', 'toolIds': [outil_id],
                      'skillIds': list(competences_ids)}, 'params': voulus}
    statut, actuel = client.appel('GET', '/api/v1/models/model?id=' + MODELE_ID)
    if statut == 200 and isinstance(actuel, dict) and actuel.get('id') == MODELE_ID:
        meta = actuel.get('meta') or {}
        pareil = (actuel.get('base_model_id') == base and _params_identiques(actuel.get('params') or {}, voulus)
                  and list(meta.get('toolIds') or []) == [outil_id] and list(meta.get('skillIds') or []) == list(competences_ids))
        if not pareil:
            client.exiger('POST', '/api/v1/models/model/update?id=' + MODELE_ID, forme)
        cree = False
    else:
        client.exiger('POST', '/api/v1/models/create', forme)
        pareil, cree = False, True
    client.exiger('POST', '/api/v1/models/model/access/update', {'id': MODELE_ID, 'access_grants': PUBLIC})
    if defaut:
        conf = client.exiger('GET', '/api/v1/configs/models') or {}
        if not conf.get('DEFAULT_MODELS'):   # un modèle par défaut choisi par le cabinet est gardé
            conf = dict(conf)
            conf['DEFAULT_MODELS'] = MODELE_ID
            client.exiger('POST', '/api/v1/configs/models', conf)
            pareil = False
    if cree:
        return _resultat('modele', 'fait', 'Modèle « AxiorHub Avocat » créé sur %s, ouvert à tout le cabinet%s.'
                         % (base, '' if not defaut else ' (choisi par défaut s’il n’y en avait aucun)'))
    return _resultat('modele', 'a_jour' if pareil else 'fait', 'Modèle « AxiorHub Avocat » ' + ('déjà à jour.' if pareil else 'mis à jour (base %s).' % base))


# ------------------------------------------------------------------------------------------------ invites
def invites(client, liste=INVITES):
    existantes = {str(x.get('command')): x for x in (client.exiger('GET', '/api/v1/prompts/') or []) if isinstance(x, dict)}
    faites = []
    for commande, nom, texte in liste:
        forme = {'command': commande, 'name': nom, 'content': texte}
        actuelle = existantes.get(commande)
        if actuelle:
            if actuelle.get('content') == texte and actuelle.get('name') == nom:
                continue
            ident = str(actuelle.get('id'))
            client.exiger('POST', '/api/v1/prompts/id/%s/update' % quote(ident, safe=''), forme)
        else:
            ident = str((client.exiger('POST', '/api/v1/prompts/create', forme) or {}).get('id') or '')
        if ident:
            client.exiger('POST', '/api/v1/prompts/id/%s/access/update' % quote(ident, safe=''), {'access_grants': PUBLIC})
        faites.append('/' + commande)
    if not faites:
        return _resultat('invites', 'a_jour', 'Invites déjà à jour.')
    return _resultat('invites', 'fait', 'Invites installées : ' + ', '.join(faites) + '.')


# ------------------------------------------------------------------------------------------------ permissions des comptes
def permissions(client):
    """Clés d'API permises aux utilisateurs (« Conversations IA » : conversation propre au dossier, compte créé par AxiorHub)."""
    perm = client.exiger('GET', '/api/v1/users/default/permissions') or {}
    if (perm.get('features') or {}).get('api_keys') is True:
        return _resultat('permissions', 'a_jour', 'Clés d’API déjà permises aux comptes du cabinet.')
    perm = json.loads(json.dumps(perm))
    perm.setdefault('features', {})['api_keys'] = True
    client.exiger('POST', '/api/v1/users/default/permissions', perm)
    return _resultat('permissions', 'fait', 'Clés d’API permises aux comptes du cabinet (conversation propre à chaque dossier).')


def administration(client, fermer_inscription=False):
    """Clés d'API activées (comptes créés par AxiorHub) ; inscription publique fermée si le cabinet l'a accepté (AxiorHub crée les comptes).
    Lecture puis écriture du réglage complet : rien d'autre n'est changé."""
    conf = client.exiger('GET', '/api/v1/auths/admin/config') or {}
    voulu = {'ENABLE_API_KEYS': True}
    if fermer_inscription:
        voulu['ENABLE_SIGNUP'] = False
    if all(conf.get(k) == v for k, v in voulu.items()):
        return _resultat('administration', 'a_jour', 'Réglages d’administration déjà conformes.')
    conf = dict(conf)
    conf.update(voulu)
    client.exiger('POST', '/api/v1/auths/admin/config', conf)
    return _resultat('administration', 'fait', 'Clés d’API activées' + (' ; inscription publique fermée (AxiorHub crée les comptes du cabinet)'
                                                                     if fermer_inscription else '') + '.')


# ------------------------------------------------------------------------------------------------ paramètres de génération
PARAMS_OPENWEBUI = ('temperature', 'top_p', 'top_k', 'min_p', 'repeat_penalty', 'repeat_last_n', 'seed')


def _params_identiques(actuels, voulus):
    return all(actuels.get(k) == v for k, v in voulus.items())


def vu_depuis_conteneur(url):
    """Une adresse de bouclage d'AxiorHub n'est pas joignable depuis le conteneur d'Open WebUI : host.docker.internal à la place."""
    p = urlsplit(url or '')
    if not p.hostname:
        return ''
    if p.hostname in ('127.0.0.1', 'localhost', '::1'):
        return '%s://host.docker.internal%s%s' % (p.scheme or 'http', (':%d' % p.port) if p.port else '', p.path.rstrip('/'))
    return str(url).rstrip('/')


def parametres_axiorhub(config, fournisseur='ollama'):
    """Paramètres de la fonction « assistant » d'AxiorHub (profil choisi : Juridique par défaut), filtrés comme AxiorHub les transmet à ce
    fournisseur (``profil5634.provider_options`` : un paramètre non pris en charge n'est pas posé ; Mistral à température 0 : top_p 1)."""
    from . import profil5634
    r = profil5634.resolve(config, 'assistant', 'complex')
    envoyes, _ = profil5634.provider_options(fournisseur, r['temperature'], r['sampling'])
    out = {k: v for k, v in envoyes.items() if k in PARAMS_OPENWEBUI}
    if fournisseur == 'ollama':
        out['num_ctx'] = profil5634.context_window(r)
        if (config.get('ollama') or {}).get('disable_thinking', True):
            out['think'] = False                       # comme AxiorHub : pas de « réflexion » longue pour Qwen et les modèles qui la proposent
    return out


def modeles_axiorhub(config):
    """{modèle: type de fournisseur} des modèles qu'AxiorHub emploie (routes des fonctions, modèles rapide, complexe et de contrôle)."""
    from .ai_gateway import provider_registry
    registre = provider_registry(config)
    routage = config.get('model_routing') or {}
    out = {}
    for cle in ('fast_model', 'complex_model', 'control_model'):
        if routage.get(cle):
            out[str(routage[cle])] = 'ollama'
    if (config.get('ollama') or {}).get('model'):
        out.setdefault(str(config['ollama']['model']), 'ollama')
    for route in routage.values():
        if isinstance(route, dict) and route.get('model'):
            out[str(route['model'])] = str((registre.get(str(route.get('provider') or 'ollama')) or {}).get('type') or 'ollama')
    for cfg in registre.values():
        if isinstance(cfg, dict) and cfg.get('model') and cfg.get('type') in COMPATIBLES and cfg.get('enabled') is not False:
            out.setdefault(str(cfg['model']), str(cfg['type']))
    return out


def parametres(client, modeles, config):
    """Chaque modèle d'AxiorHub proposé par Open WebUI reçoit les mêmes paramètres de génération que dans AxiorHub (réglage du modèle de
    base dans Open WebUI) ; un modèle qu'Open WebUI ne propose pas n'est pas créé."""
    proposes = set(modeles_disponibles(client))
    faits, deja, absents = [], [], []
    for ident, genre in sorted(modeles.items()):
        if ident not in proposes:
            absents.append(ident)
            continue
        voulus = parametres_axiorhub(config, genre)
        statut, actuel = client.appel('GET', '/api/v1/models/model?id=' + quote(ident, safe=''))
        if statut == 200 and isinstance(actuel, dict) and actuel.get('id') == ident:
            if _params_identiques(actuel.get('params') or {}, voulus):
                deja.append(ident)
                continue
            forme = {'id': ident, 'base_model_id': actuel.get('base_model_id'), 'name': actuel.get('name') or ident,
                     'meta': actuel.get('meta') or {}, 'params': dict(actuel.get('params') or {}, **voulus)}
            client.exiger('POST', '/api/v1/models/model/update?id=' + quote(ident, safe=''), forme)
        else:
            client.exiger('POST', '/api/v1/models/create', {'id': ident, 'base_model_id': None, 'name': ident, 'meta': {}, 'params': voulus})
            client.exiger('POST', '/api/v1/models/model/access/update', {'id': ident, 'access_grants': PUBLIC})
        faits.append('%s (%s)' % (ident, ', '.join('%s %s' % (k, v) for k, v in voulus.items())))
    note = (' Non proposés par Open WebUI : %s.' % ', '.join(absents)) if absents else ''
    if not faits:
        return _resultat('parametres', 'a_jour' if deja else 'saute', ('Paramètres déjà identiques à AxiorHub.' if deja else
                                                                       'Aucun modèle d’AxiorHub proposé par Open WebUI.') + note)
    return _resultat('parametres', 'fait', 'Paramètres alignés sur AxiorHub : ' + ' ; '.join(faits) + '.' + note)


# ------------------------------------------------------------------------------------------------ voix (synthèse et dictée)
WHISPER = {'rapide': 'base', 'equilibre': 'small', 'precis': 'large-v3-turbo'}   # tailles des modèles de dictée d'AxiorHub (whisper.cpp)


def voix_axiorhub(config, dictee=None, vitesse=1.0, cle_voix=''):
    """(réglage TTS, réglage STT, remarques) d'Open WebUI correspondant aux voix d'AxiorHub. ``cle_voix`` : clé ElevenLabs acceptée."""
    from .voix5694 import fournisseur
    sp = config.get('speech568') or {}
    choix = fournisseur(config)
    notes = []
    if choix in ('kokoro', 'chatterbox'):
        tts = {'ENGINE': 'openai', 'OPENAI_API_BASE_URL': vu_depuis_conteneur(str(sp.get('url') or 'http://127.0.0.1:8880')) + '/v1',
               'OPENAI_API_KEY': '', 'MODEL': str(sp.get('model') or choix),
               'VOICE': str(sp.get('voice') or ('ff_siwis' if choix == 'kokoro' else 'default')), 'OPENAI_PARAMS': {'speed': float(vitesse)}}
        notes.append('voix %s « %s » (même serveur de voix qu’AxiorHub)' % (choix, tts['VOICE']))
    elif choix == 'elevenlabs' and cle_voix:
        tts = {'ENGINE': 'elevenlabs', 'API_KEY': cle_voix, 'VOICE': str(sp.get('voice') or ''), 'MODEL': str(sp.get('model') or 'eleven_multilingual_v2')}
        notes.append('voix ElevenLabs « %s »' % tts['VOICE'])
    else:
        tts = {'ENGINE': ''}
        notes.append({'brindille': 'la voix Brindille d’AxiorHub ne tourne que dans AxiorHub : Open WebUI lit avec la voix française du navigateur',
                      'espeak': 'eSpeak n’a pas d’interface pour Open WebUI : Open WebUI lit avec la voix française du navigateur',
                      'elevenlabs': 'clé ElevenLabs non transmise : Open WebUI lit avec la voix du navigateur'}.get(
                          choix, 'voix du navigateur'))
    modele = WHISPER.get(str((dictee or {}).get('modele') or 'equilibre'), 'small')
    stt = {'ENGINE': '', 'WHISPER_MODEL': modele}
    notes.append('dictée Whisper « %s » comme la dictée d’AxiorHub (téléchargé par Open WebUI au premier usage)' % modele)
    return tts, stt, notes


def voix(client, tts, stt, notes=()):
    actuel = client.exiger('GET', '/api/v1/audio/config') or {}
    t, s = dict(actuel.get('tts') or {}), dict(actuel.get('stt') or {})
    if all(t.get(k) == v for k, v in tts.items()) and all(s.get(k) == v for k, v in stt.items()):
        return _resultat('voix', 'a_jour', 'Voix déjà alignées sur AxiorHub : ' + ' ; '.join(notes) + '.')
    t.update(tts)
    s.update(stt)
    statut, _ = client.appel('POST', '/api/v1/audio/config/update', {'tts': t, 'stt': s})
    if statut in (401, 403):
        raise ErreurOpenWebUI('cle_admin_refusee')
    relu = client.exiger('GET', '/api/v1/audio/config') or {}   # 0.13.5 : réglage enregistré puis erreur 500 si Whisper ne peut se charger
    ok = (all((relu.get('tts') or {}).get(k) == v for k, v in tts.items()) and all((relu.get('stt') or {}).get(k) == v for k, v in stt.items()))
    if not ok:
        return _resultat('voix', 'echec', 'Open WebUI n’a pas enregistré les réglages de voix (http_%d).' % statut)
    return _resultat('voix', 'fait', 'Voix alignées sur AxiorHub : ' + ' ; '.join(notes) + '.')


# ------------------------------------------------------------------------------------------------ enchaînement
ETAPES = ('administration', 'permissions', 'outil', 'connexions', 'mcp', 'competences', 'parametres', 'modele', 'invites', 'voix')


def regler(client, entrees, etapes=ETAPES, journal=None):
    """``entrees`` : source, api_url, jeton, systeme, fournisseurs, cles_fournisseurs, ollama_url, connecteurs, cles_mcp, ecartes_mcp,
    competences, modele_prefere, config (AxiorHub, pour les paramètres et les voix), tts, stt, notes_voix. Une étape en échec n'arrête pas
    les suivantes (sauf la clé refusée ou Open WebUI injoignable) ; le résultat la dit."""
    out, ids = [], []
    for nom in etapes:
        try:
            if nom == 'administration':
                r = administration(client, bool(entrees.get('fermer_inscription')))
            elif nom == 'permissions':
                r = permissions(client)
            elif nom == 'parametres':
                r = parametres(client, modeles_axiorhub(entrees.get('config') or {}), entrees.get('config') or {})
            elif nom == 'voix':
                if not entrees.get('tts'):
                    continue
                r = voix(client, entrees['tts'], entrees['stt'], entrees.get('notes_voix') or ())
            elif nom == 'outil':
                r = outil(client, entrees['source'], entrees['api_url'], entrees['jeton'])
            elif nom == 'connexions':
                r = connexions(client, entrees.get('fournisseurs') or [], entrees.get('cles_fournisseurs') or {}, entrees.get('ollama_url') or '')
            elif nom == 'mcp':
                r = mcp(client, entrees.get('connecteurs') or [], entrees.get('cles_mcp') or {}, entrees.get('ecartes_mcp') or ())
            elif nom == 'competences':
                r, ids = competences(client, entrees.get('competences') or [])
            elif nom == 'modele':
                base = choisir_base(modeles_disponibles(client), entrees.get('modele_prefere') or '')
                genre = (modeles_axiorhub(entrees.get('config') or {}).get(base)
                         or ('ollama' if proprietaires(client).get(base) == 'ollama' else 'openai'))
                r = modele(client, entrees['systeme'], base, ids,
                           params=parametres_axiorhub(entrees['config'], genre) if entrees.get('config') else None,
                           outil_id=outil_existant(client) or OUTIL_ID)
            elif nom == 'invites':
                r = invites(client)
            else:
                continue
        except ErreurOpenWebUI as ex:
            if str(ex) in ('cle_admin_refusee', 'openwebui_injoignable'):
                raise
            r = _resultat(nom, 'echec', 'Open WebUI a refusé cette étape (%s).' % ex)
        out.append(r)
        if journal:
            journal(r)
    return out
