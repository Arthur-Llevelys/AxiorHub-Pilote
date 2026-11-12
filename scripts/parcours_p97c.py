"""5.6.97 (lot chatgpt) : parcours P97c du banc ``scripts/parcours-navigateur.py`` (vrai navigateur, données fictives, aucun réseau).

Chargé par le banc ; utilise ses outils (``verifier``) par le module principal. OpenAI est simulé dans le processus du banc
(``FakeOpenAI`` des essais : clés RSA d'essai, aucun appel réseau) ; la page d'OpenAI ouverte dans l'onglet est une page fictive servie par
le navigateur lui-même (interception), et le retour d'OpenAI est joué par le banc sur l'écoute 127.0.0.1 du poste.

- P97c1 Poste : « Continuer avec ChatGPT » ouvre l'onglet, l'état passe à « Abonnement autorisé » sans recharger la page ; aucun code
  d'autorisation affiché ; bouton rendu ensuite.
- P97c2 Serveur (option ``liaison_ia`` active, navigateur distant simulé) : « Ouvrir AxiorHub Poste », code court affiché à l'approbation,
  bouton atteint au clavier, autorisation, réception vérifiée côté serveur, état mis à jour sans rechargement ; « Autres méthodes » fermé
  et intact.
Le poste lui-même (protocole axiorhub://, fenêtre du poste) est simulé par des appels directs : il n'est pas exercé dans ce parcours.
"""
import hashlib
import json
from pathlib import Path
import secrets
import sys
import urllib.parse
import urllib.request


def _nav():
    import __main__ as nav
    return nav


def _faux_openai():
    root = Path(__file__).resolve().parent.parent
    if str(root / 'tests') not in sys.path:
        sys.path.insert(0, str(root / 'tests'))
    from test_v5662 import FakeOpenAI
    return FakeOpenAI()


def _attendre_etat(p, carte, texte, delai=20000):
    p.wait_for_function('([sel, t]) => { const n = document.querySelector(sel); return !!n && n.textContent.indexOf(t) >= 0; }',
                        arg=['#chatgpt5662 [data-c5697-etat]', texte], timeout=delai)


def _ouvrir_carte(p):
    """La section ChatGPT est pliée dans Modèles d'IA : on la déplie comme l'utilisateur, par son titre."""
    pli = p.locator('details:has(#chatgpt5662) > summary')
    if pli.count() and not p.evaluate("document.querySelector('#chatgpt5662').closest('details').open"):
        pli.first.click()
    p.locator('#chatgpt5662').scroll_into_view_if_needed()


def p97c_chatgpt(banc, s):
    """Connexion ChatGPT suivie (poste), puis liaison poste → serveur (serveur) ; aucune requête hors du banc, aucun rechargement."""
    nav = _nav()
    verifier = nav.verifier
    from agent import chatgpt5662 as cg, chatgpt5697 as c97
    from agent.chatgpt5682 import RECORD_FIELDS
    from agent.common import private_json
    from agent import web5662
    oa = _faux_openai()
    vrai_http, vrai_local = cg.http, web5662.local_browser
    cg.http = oa
    try:
        p = s.page
        s.connecter()
        # ---------------------------------------------------------------- P97c1 : poste, tentative suivie
        s.context.route('https://auth.openai.com/**', lambda route: route.fulfill(status=200, content_type='text/html; charset=utf-8',
                                                                                  body='<!doctype html><title>OpenAI (fictif)</title><p>Page fictive du banc.</p>'))
        s.aller('/ia-externe')
        _ouvrir_carte(p)
        carte = p.locator('#chatgpt5662')
        verifier(carte.locator('form[data-mode="poste"]').count() == 1, 'poste : formulaire de connexion suivie absent')
        p.evaluate('window.__p97c = 1')
        with s.context.expect_page() as onglet:
            with p.expect_response(lambda r: '/m5662/chatgpt/connecter' in r.url) as rep:
                carte.locator('form[data-mode="poste"] button[type=submit]').click()
        d = rep.value.json()
        verifier(rep.value.status == 200 and d.get('tentative') and d.get('mode') == 'poste', 'poste : tentative non créée')
        page_openai = onglet.value
        page_openai.wait_for_url('https://auth.openai.com/**', timeout=15000)
        verifier(page_openai.url.startswith('https://auth.openai.com/'), 'poste : l’onglet n’a pas ouvert la page d’OpenAI')
        q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(d['url']).query))
        oa.nonce = q['nonce']
        retour = q['redirect_uri'] + '?' + urllib.parse.urlencode({'code': 'c0de-banc', 'state': q['state'], 'client_id': oa.client_id})
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(retour, timeout=30) as r:
            page_retour = r.read().decode('utf-8')
        verifier('c0de-banc' not in page_retour and 'Revenir à AxiorHub' in page_retour, 'poste : la page de retour montre plus qu’un état')
        page_openai.close()
        _attendre_etat(p, carte, 'Abonnement autorisé')
        verifier(p.evaluate('window.__p97c') == 1, 'poste : la page a été rechargée')
        verifier('c0de-banc' not in p.content(), 'poste : le code d’autorisation est affiché')
        verifier(carte.locator('form[data-mode="poste"] button[type=submit]').is_enabled(), 'poste : bouton non rendu après la connexion')
        if getattr(banc, 'sortie', None):
            carte.screenshot(path=str(Path(banc.sortie) / 'P97c1-poste.png'))
        s.propre('connexion suivie sur le poste')
        # ---------------------------------------------------------------- P97c2 : serveur, liaison avec AxiorHub Poste
        private_json(banc.config_path, {**banc.config, 'liaison_ia': {'active': True}})
        # navigateur sur une autre machine que le serveur (relais Apache) : le banc sert en direct sur 127.0.0.1, la détection est donc forcée
        from agent import web5662
        web5662.local_browser = lambda env: False
        s.aller('/ia-externe')
        _ouvrir_carte(p)
        carte = p.locator('#chatgpt5662')
        verifier(carte.locator('form[data-mode="liaison"]').count() == 1, 'serveur : liaison absente alors que l’option est active')
        autres = carte.locator('details.c5697-autres')
        verifier(not autres.evaluate('d => d.open') and autres.locator('form[data-api="m5662/chatgpt/terminer"]').count() == 1,
                 'serveur : « Autres méthodes » absentes ou ouvertes')
        p.evaluate('window.__p97c = 2')
        with p.expect_response(lambda r: '/m5662/chatgpt/connecter' in r.url) as rep:
            carte.locator('form[data-mode="liaison"] button[type=submit]').click()
        d = rep.value.json()
        lien = carte.locator('[data-c5697-zone] a', has_text='Ouvrir AxiorHub Poste')
        verifier(d.get('mode') == 'liaison' and lien.get_attribute('href') == d['lien'] and d['lien'].startswith('axiorhub://chatgpt/liaison?id='),
                 'serveur : lien axiorhub:// absent')
        lid = c97.lire_lien(d['lien'])
        preuve = secrets.token_urlsafe(32)
        with banc.desk() as desk:   # AxiorHub Poste simulé : revendication avec l'empreinte de sa preuve
            code = c97.reclamer(desk, lid, hashlib.sha256(preuve.encode()).hexdigest(), {})['code']
        bouton = carte.locator('button', has_text='Les codes sont identiques : autoriser')
        bouton.wait_for(timeout=15000)
        verifier(('Code à comparer : ' + code) in carte.inner_text(), 'serveur : code court absent ou différent')
        verifier(p.evaluate('document.activeElement && document.activeElement.textContent') == 'Les codes sont identiques : autoriser',
                 'serveur : le bouton d’autorisation n’a pas le focus clavier')
        if getattr(banc, 'sortie', None):
            carte.screenshot(path=str(Path(banc.sortie) / 'P97c2-code.png'))
        with p.expect_response(lambda r: '/m5662/chatgpt/liaison/approuver' in r.url) as rep:
            p.keyboard.press('Enter')
        verifier(rep.value.status == 200, 'serveur : autorisation refusée')
        with banc.desk() as desk:   # le poste lit le nonce, mène la connexion d'OpenAI et transmet la session
            etat = c97.etat_poste(desk, lid, preuve, {})
            verifier(etat['etat'] == 'autorisee' and etat['instance'] == cg.host_id(desk), 'serveur : liaison non autorisée pour le poste')
            oa.nonce = etat['nonce']
            cand = cg.exchange(desk, {'code': 'c0de-banc'}, {'verifier': 'v' * 43, 'redirect_uri': 'http://127.0.0.1:1455/auth/callback',
                                                              'nonce': etat['nonce']}, etat['client_id'] or oa.client_id)
            out = c97.recevoir(desk, lid, preuve, {k: cand['record'][k] for k in RECORD_FIELDS}, {})
        verifier(out['etat'] == 'recue', 'serveur : session non publiée (%s)' % json.dumps(out))
        p.wait_for_function('() => (document.querySelector("#chatgpt5662 .c5697-suivi") || {}).textContent.indexOf("Abonnement autorisé") >= 0', timeout=20000)
        verifier(p.evaluate('window.__p97c') == 2, 'serveur : la page a été rechargée')
        verifier(len([c for c in oa.calls if c[1].endswith('/responses')]) == 1, 'serveur : test de génération absent ou répété')
        if getattr(banc, 'sortie', None):
            carte.screenshot(path=str(Path(banc.sortie) / 'P97c2-serveur.png'))
        s.propre('liaison poste → serveur')
        verifier(banc.modele.nombre() == 0 if callable(getattr(banc.modele, 'nombre', None)) else True, 'un modèle a été appelé')
    finally:
        cg.http, web5662.local_browser = vrai_http, vrai_local
