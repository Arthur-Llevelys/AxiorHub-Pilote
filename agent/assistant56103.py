"""5.6.103 (lot G) : étapes facultatives « Compte Google » et « Compte Microsoft » de la mise en service.

Où elles apparaissent :
- **guide d'installation pas à pas** (``guide5631``, serveur et poste ; c'est aussi l'écran du premier lancement sur le poste) : à l'étape
  « Messagerie » et sur l'écran de fin, quelle que soit la liste des fonctions choisies ;
- **page d'installation** du serveur (``setup560``, ``/installation``) : état des deux étapes et « Passer » ;
- **Paramètres › Connexions** : même carte, pour reprendre plus tard une étape passée ou à refaire.

Chaque étape est **passable** et **reprise plus tard**. Son état affiché est toujours l'**état réel**, recalculé à chaque lecture sans rien
écrire (R05) : ``faite`` (connexion constatée), ``a_refaire`` (dernier essai en échec, autorisation retirée, secret modifié depuis le test),
``passee`` (passée par la personne et pas faite), ``a_faire``, et pour Microsoft ``indisponible`` (« pas encore disponible »).
« Passer » ne fait que noter le choix de la personne ; « Reprendre » efface cette note : rien d'autre n'est modifié, aucune connexion n'est
ouverte, aucun secret n'est lu.

- **Google** : chemin IMAP par mot de passe d'application (``gmail_simple56103``, essai noté et rattaché au secret en service) ou connexion
  OAuth de la personne sur le connecteur existant (``google5697`` / ``gmail5697``, application interne Workspace du cabinet).
- **Microsoft** : point de montage du lot M, ``agent/microsoft56103.py`` (fonction ``etapes_assistant``, documentée en tête de ce module).
  Module absent, fonction absente, application intégrée non renseignée (``disponible`` faux) ou erreur : l'étape s'affiche « pas encore
  disponible », sans erreur. Contrat attendu (souple) : un dictionnaire ``{'disponible': bool, 'etat': 'faite'|'a_faire'|'a_refaire',
  'message': str, 'lien': str}`` ; d'autres libellés usuels sont acceptés (``connecte``, ``a_reconnecter``…). Les paramètres ``desk``,
  ``owner`` et ``mode`` (« poste » ou « serveur ») sont passés selon la signature de la fonction.
"""
from datetime import datetime, timezone
from html import escape as e
import hashlib
import inspect
import json
import re

from .common import Stop

ETAPES = ('google', 'microsoft')
ROLES = ('administrateur', 'avocat', 'assistant')
PREFIXE = 'm56103/assistant/'
ROUTES_GET = ('etat',)
ROUTES_POST = ('passer', 'reprendre')
CLE = re.compile(r'^[A-Za-z0-9-]{8,64}$')
LIBELLES = {'faite': 'Faite', 'a_refaire': 'À refaire', 'passee': 'Passée (à reprendre quand vous voulez)', 'a_faire': 'À faire (facultatif)',
            'indisponible': 'Pas encore disponible'}
TITRES = {'google': 'Compte Google', 'microsoft': 'Compte Microsoft'}
ANCRES = {'google': '#gmail56103', 'microsoft': '#microsoft56103'}
RETOUR = '/parametres?rubrique=connexions'

SCHEMA = '''
CREATE TABLE IF NOT EXISTS assistant56103_etapes(owner TEXT NOT NULL, etape TEXT NOT NULL, statut TEXT NOT NULL, quand TEXT NOT NULL,
  PRIMARY KEY(owner, etape));
CREATE TABLE IF NOT EXISTS assistant56103_recus(compte TEXT NOT NULL, route TEXT NOT NULL, cle TEXT NOT NULL, empreinte TEXT NOT NULL,
  reponse TEXT NOT NULL, quand TEXT NOT NULL, PRIMARY KEY(compte, route, cle));
'''
MESSAGES = {'etape_inconnue': 'Étape inconnue.', 'cle_idempotence_requise': 'Page incomplète : rechargez-la.',
            'cle_idempotence_deja_utilisee': 'Cette demande a déjà été traitée avec d’autres valeurs : rechargez la page.',
            'role_insuffisant': 'Action réservée.', 'route_inconnue': 'Route inconnue.'}
STATUTS = {'role_insuffisant': '403 Forbidden', 'route_inconnue': '404 Not Found', 'etape_inconnue': '404 Not Found',
           'cle_idempotence_deja_utilisee': '409 Conflict'}


def _now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def ensure_schema(desk):
    """Création idempotente, au démarrage (``Desk``) seulement ; le schéma de ``gmail_simple56103`` est créé au même moment."""
    desk.db.executescript(SCHEMA)
    desk.db.commit()
    from .gmail_simple56103 import ensure_schema as gmail_schema
    gmail_schema(desk)
    from .avocatfr56106 import ensure_schema as avocatfr_schema   # 5.6.106 (lot P) : essais de l'adresse @avocat.fr, créés au même moment
    avocatfr_schema(desk)


# ------------------------------------------------------------------------------------------------ reçus d'idempotence (partagés avec gmail_simple56103)
def recu(desk, compte, route, cle, empreinte):
    if not CLE.fullmatch(str(cle or '')):
        raise Stop('cle_idempotence_requise')
    r = desk.db.execute('SELECT empreinte, reponse FROM assistant56103_recus WHERE compte=? AND route=? AND cle=?', (compte, route, cle)).fetchone()
    if r and r['empreinte'] != empreinte:
        raise Stop('cle_idempotence_deja_utilisee')
    return json.loads(r['reponse']) if r else None


def garder(desk, compte, route, cle, empreinte, out):
    desk.db.execute('INSERT OR IGNORE INTO assistant56103_recus VALUES(?,?,?,?,?,?)', (compte, route, cle, empreinte, json.dumps(out, ensure_ascii=False, default=str), _now()))
    desk.db.commit()


# ------------------------------------------------------------------------------------------------ état réel (lecture seule)
def _marques(desk, owner):
    try:
        return {r['etape']: r['statut'] for r in desk.db.execute('SELECT etape, statut FROM assistant56103_etapes WHERE owner=?', (owner,))}
    except Exception:   # noqa: BLE001 — table absente : aucune étape passée (jamais une étape déclarée faite)
        return {}


def mode(desk):
    """« poste » (connexion Microsoft par PKCE dans le navigateur) ou « serveur » (code d'appareil)."""
    try:
        from .diagnostic5634 import install_mode
        return 'serveur' if install_mode(desk.c) == 'serveur' else 'poste'
    except Exception:   # noqa: BLE001
        return 'serveur'


def etat_google(desk, owner):
    from . import gmail_simple56103 as gs
    statut, phrase = gs.etat_texte(desk)
    oauth = ''
    try:
        from . import gmail5697 as gm, google5697 as gg
        oauth = gg.vue(gm.connexion_de(desk, owner)).get('etat', '')
    except Exception:   # noqa: BLE001 — tables Gmail pas encore créées : aucune connexion OAuth constatée
        oauth = ''
    if statut == 'ok' or oauth == 'active':
        detail = phrase if statut == 'ok' else 'Gmail connecté à votre compte par l’application interne du cabinet.'
        return {'etat': 'faite', 'message': detail}
    if oauth == 'a_reconnecter':
        return {'etat': 'a_refaire', 'message': 'Google a retiré l’autorisation de votre connexion Gmail : reconnectez-la.'}
    if statut in ('echec', 'a_tester'):
        return {'etat': 'a_refaire', 'message': phrase}
    return {'etat': 'a_faire', 'message': 'Mot de passe d’application Gmail (le plus simple) ou application interne Google Workspace.'}


ETATS_MS = {'faite': 'faite', 'fait': 'faite', 'connecte': 'faite', 'connectee': 'faite', 'active': 'faite', 'ok': 'faite',
            'a_refaire': 'a_refaire', 'a_reconnecter': 'a_refaire', 'erreur': 'a_refaire', 'echec': 'a_refaire', 'expiree': 'a_refaire'}


def _montage_microsoft():
    import importlib
    try:
        module = importlib.import_module('.microsoft56103', __package__)
    except Exception:   # noqa: BLE001 — module du lot M absent ou cassé : « pas encore disponible »
        return None
    fonction = getattr(module, 'etapes_assistant', None)
    return fonction if callable(fonction) else None


def _appeler(fonction, desk, owner, env):
    """Paramètres passés selon la signature (contrat du lot M : ``etapes_assistant(desk, owner, env=None)``)."""
    try:
        params = inspect.signature(fonction).parameters
    except (TypeError, ValueError):
        params = {}
    if not params:
        return fonction(desk, owner)
    valeurs = {'desk': desk, 'owner': owner, 'env': env, 'mode': mode(desk)}
    if 'desk' in params:
        return fonction(**{k: v for k, v in valeurs.items() if k in params})
    return fonction(desk, owner)


def etat_microsoft(desk, owner, env=None):
    indisponible = {'etat': 'indisponible', 'message': 'Connexion Microsoft intégrée pas encore disponible. Vous pouvez passer cette étape ; '
                                                       'l’agenda Outlook avec l’application propre du cabinet reste réglable dans Paramètres › Connexions.', 'lien': ''}
    fonction = _montage_microsoft()
    if fonction is None:
        return indisponible
    try:
        brut = _appeler(fonction, desk, owner, env)
    except Exception:   # noqa: BLE001 — une panne du lot M ne casse jamais l'assistant
        return indisponible
    autres = []
    if isinstance(brut, (list, tuple)):
        lignes = [x for x in brut if isinstance(x, dict)]
        brut = next((x for x in lignes if x.get('id') in ('microsoft.compte', 'microsoft') or x.get('etape') == 'microsoft'), lignes[0] if lignes else None)
        autres = [x for x in lignes if x is not brut]
    if not isinstance(brut, dict):
        return indisponible
    etat_brut = str(brut.get('etat') or brut.get('statut') or '').lower()
    if brut.get('disponible') is False or etat_brut == 'indisponible':
        texte = str(brut.get('message') or brut.get('detail') or '')[:400]
        return {**indisponible, 'message': (texte + ' ' if texte else '') + 'Vous pouvez passer cette étape et la reprendre plus tard.'}
    etat = ETATS_MS.get(etat_brut, 'a_faire')
    texte = str(brut.get('message') or brut.get('detail') or brut.get('libelle') or '')[:400]
    if etat == 'faite' and autres:
        faites = [str(x.get('titre') or '') for x in autres if ETATS_MS.get(str(x.get('etat') or '').lower()) == 'faite']
        texte += ' Autorisations accordées : %s.' % (', '.join(faites) if faites else 'aucune pour l’instant')
    lien = str(brut.get('lien') or brut.get('url') or '')
    if lien and not (lien.startswith('/') and not lien.startswith('//')):
        lien = ''                                  # lien interne seulement (jamais une adresse externe injectée dans la page)
    defaut = {'faite': 'Compte Microsoft connecté.', 'a_refaire': 'Connexion Microsoft à refaire.',
              'a_faire': 'Connexion par l’application Microsoft intégrée : %s.' % ('dans le navigateur de ce poste' if mode(desk) == 'poste' else 'par code d’appareil')}
    return {'etat': etat, 'message': texte.strip() or defaut[etat], 'lien': lien}


def etat(desk, owner, env=None):
    """État réel des deux étapes pour la personne : jamais d'écriture."""
    marques = _marques(desk, owner)
    out = {}
    for etape, calcul in (('google', etat_google), ('microsoft', lambda d, o: etat_microsoft(d, o, env))):
        try:
            r = calcul(desk, owner)
        except Exception:   # noqa: BLE001 — source en panne : état « à refaire » dit tel quel, jamais un faux « faite »
            r = {'etat': 'a_refaire', 'message': 'État illisible pour le moment : rouvrez l’étape.'}
        reel = r['etat']
        affiche = 'passee' if marques.get(etape) == 'passee' and reel in ('a_faire', 'indisponible') else reel
        out[etape] = {'etape': etape, 'titre': TITRES[etape], 'etat': affiche, 'reel': reel, 'libelle': LIBELLES[affiche],
                      'message': r.get('message', ''), 'lien': r.get('lien', ''), 'passee': marques.get(etape) == 'passee'}
    return out


# ------------------------------------------------------------------------------------------------ passer / reprendre (POST)
def passer(desk, owner, etape):
    if etape not in ETAPES:
        raise Stop('etape_inconnue')
    desk.db.execute('INSERT OR REPLACE INTO assistant56103_etapes(owner, etape, statut, quand) VALUES(?,?,?,?)', (owner, etape, 'passee', _now()))
    desk.db.commit()
    desk.audit('assistant56103_passee', {'etape': etape})
    return {'etape': etape, 'etat': etat(desk, owner)[etape], 'message': '%s : étape passée. Vous pourrez la reprendre dans Paramètres › Connexions.' % TITRES[etape]}


def reprendre(desk, owner, etape, prefix=''):
    if etape not in ETAPES:
        raise Stop('etape_inconnue')
    desk.db.execute('DELETE FROM assistant56103_etapes WHERE owner=? AND etape=?', (owner, etape))
    desk.db.commit()
    desk.audit('assistant56103_reprise', {'etape': etape})
    courant = etat(desk, owner)[etape]
    return {'etape': etape, 'etat': courant, 'lien': courant.get('lien') or (prefix + RETOUR + ANCRES[etape]),
            'message': '%s : étape reprise (%s).' % (TITRES[etape], courant['libelle'].lower())}


# ------------------------------------------------------------------------------------------------ routes /api440/m56103/assistant/*
def allowed(role, method, api):
    route = str(api or '').split('?', 1)[0]
    route = route[len(PREFIXE):] if route.startswith(PREFIXE) else ''
    return role in ROLES and route in (ROUTES_GET if method == 'GET' else ROUTES_POST if method == 'POST' else ())


def _out(value, status='200 OK'):
    return {'status': status, 'kind': 'application/json; charset=utf-8', 'body': json.dumps(value, ensure_ascii=False, default=str),
            'headers': [('Cache-Control', 'private, no-store')]}


def api(env, desk, auth, prefix, name, args, method):
    try:
        return _api(env, desk, auth, prefix, name, args, method)
    except Stop as erreur:
        code = str(erreur).split(':')[0]
        return _out({'error': code, 'message': MESSAGES.get(code) or code.replace('_', ' ')}, STATUTS.get(code, '400 Bad Request'))


def _api(env, desk, auth, prefix, name, args, method):
    from .web567 import actor
    owner, role = actor(env)
    route = name.split('?', 1)[0][len(PREFIXE):] if name.startswith(PREFIXE) else ''
    if not allowed(role, method, PREFIXE + route):
        raise Stop('route_inconnue' if route not in ROUTES_GET + ROUTES_POST else 'role_insuffisant')
    if method == 'GET':
        return _out({'etapes': etat(desk, owner, env), 'mode': mode(desk)})
    from .web440 import check_post
    data = check_post(env, auth)
    etape = str(data.get('etape') or '')
    if etape not in ETAPES:
        raise Stop('etape_inconnue')
    cle = str(data.get('cle') or '')
    empreinte = hashlib.sha256(json.dumps([route, etape]).encode()).hexdigest()
    ancien = recu(desk, owner, 'assistant/' + route, cle, empreinte)
    if ancien is not None:
        return _out(ancien)
    out = passer(desk, owner, etape) if route == 'passer' else reprendre(desk, owner, etape, prefix)
    garder(desk, owner, 'assistant/' + route, cle, empreinte, out)
    return _out(out)


# ------------------------------------------------------------------------------------------------ présentation
def _bouton(route, etape, libelle, classe='ax-btn ghost'):
    from .ui_components5689 import formulaire
    import uuid
    return formulaire('m56103/assistant/' + route, '<input type="hidden" name="etape" value="%s"><input type="hidden" name="cle" value="%s">'
                      '<button class="%s" type="submit">%s</button>' % (e(etape, quote=True), uuid.uuid4().hex, e(classe, quote=True), e(libelle)),
                      recharge=True, classe='as103-inline')


def etapes_html(desk, prefix, owner, avec_liens=True, env=None):
    """Liste des deux étapes (état réel, explication, Passer / Reprendre, lien vers le réglage)."""
    lignes = ''
    for etape, r in etat(desk, owner, env).items():
        actions = ''
        if r['etat'] in ('a_faire', 'indisponible', 'a_refaire') and not r['passee']:
            actions += _bouton('passer', etape, 'Passer cette étape')
        if r['passee']:
            actions += _bouton('reprendre', etape, 'Reprendre cette étape', 'ax-btn')
        if avec_liens and r['etat'] != 'indisponible':
            lien = r['lien'] or (prefix + RETOUR + ANCRES[etape])
            actions += '<p><a href="%s">%s</a></p>' % (e(lien, quote=True), 'Ouvrir le réglage' if r['etat'] == 'faite' else 'Configurer maintenant')
        lignes += ('<li data-etape="%s" data-etat="%s"><p><strong>%s</strong> — <span>%s</span></p><p class="ax-muted">%s</p>%s</li>') % (
            e(etape, quote=True), e(r['etat'], quote=True), e(r['titre']), e(r['libelle']), e(r['message']), actions)
    return '<ul class="as103-etapes">%s</ul>' % lignes


def section_html(desk, prefix, owner, role, env=None):
    """Carte de Paramètres › Connexions : reprendre une étape passée ou à refaire de la mise en service."""
    return ('<section class="ax-card" id="mise56103" aria-labelledby="mise56103-t"><h2 id="mise56103-t">Mise en service : comptes Google et Microsoft '
            '(facultatif)</h2><p class="ax-muted">Étapes facultatives de l’assistant de mise en service. L’état affiché est celui constaté maintenant.</p>%s</section>') % (
        etapes_html(desk, prefix, owner, env=env))


def carte_guide(desk, env, prefix, admin=False, owner=''):
    """Carte appelée par le guide d'installation (``guide5631.CARTES['comptes']``), hors du formulaire du guide."""
    from . import gmail_simple56103 as gs
    corps = etapes_html(desk, prefix, owner or 'cabinet', avec_liens=False, env=env)
    corps += gs.section_html(desk, prefix, owner or 'cabinet', 'administrateur' if admin else 'avocat', env)
    ms = etat(desk, owner or 'cabinet', env)['microsoft']
    if ms['etat'] != 'indisponible':
        try:   # carte du lot M (même carte que Paramètres › Connexions) ; absente ou en panne : lien vers le réglage
            from .microsoft_web56103 import section_html as carte_microsoft
            corps += carte_microsoft(desk, prefix, owner or 'cabinet', env)
        except Exception:   # noqa: BLE001
            corps += '<p><a class="ax-btn ghost" href="%s">Connecter le compte Microsoft</a></p>' % e(ms['lien'] or (prefix + RETOUR + ANCRES['microsoft']), quote=True)
    return ('<section class="g5631-sub as103-comptes"><h3>Comptes Google et Microsoft (facultatif)</h3><p>Ces étapes peuvent être passées et reprises '
            'plus tard dans Paramètres › Connexions.</p>%s</section>') % corps


carte_guide.avec_compte = True   # guide5631._carte : le compte de la personne est transmis


# ------------------------------------------------------------------------------------------------ page d'installation du serveur (setup560)
def installation_html(desk, owner, csrf, prefix=''):
    """Bloc de ``/installation`` : état réel des deux étapes, « Passer » ou « Reprendre » (formulaire classique de la page, jeton CSRF de la
    page), configuration dans Paramètres › Connexions une fois l'installation enregistrée."""
    lignes = ''
    for etape, r in etat(desk, owner).items():
        op = ('reprendre_' if r['passee'] else 'passer_') + etape
        bouton = '' if r['etat'] == 'faite' else ('<button name="op" value="%s" class="ghost">%s</button>' % (
            e(op, quote=True), 'Reprendre cette étape' if r['passee'] else 'Passer cette étape'))
        lignes += '<li data-etape="%s" data-etat="%s"><strong>%s</strong> — %s. <span class="muted">%s</span> %s</li>' % (
            e(etape, quote=True), e(r['etat'], quote=True), e(r['titre']), e(r['libelle']), e(r['message']), bouton)
    return ('<form method="post" action="%s/installation"><input type="hidden" name="csrf" value="%s"><fieldset><legend>7 · Comptes Google et Microsoft '
            '(facultatif)</legend><ul>%s</ul><p class="muted">Ces étapes se font ou se reprennent plus tard dans <a href="%s%s">Paramètres › Connexions</a> : '
            'mot de passe d’application Gmail ou application interne Google Workspace ; connexion Microsoft par l’application intégrée quand elle sera '
            'disponible.</p></fieldset></form>') % (e(prefix, quote=True), e(csrf, quote=True), lignes, e(prefix, quote=True), e(RETOUR + '#mise56103', quote=True))


def installation_action(desk, owner, op):
    """« passer_google », « reprendre_microsoft »… depuis ``/installation`` ; rend la phrase de résultat."""
    action, _, etape = str(op or '').partition('_')
    if action not in ('passer', 'reprendre') or etape not in ETAPES:
        raise Stop('etape_inconnue')
    out = passer(desk, owner, etape) if action == 'passer' else reprendre(desk, owner, etape)
    return out['message']
