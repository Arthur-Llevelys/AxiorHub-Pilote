#!/usr/bin/env python3
"""Test « fumée » de la démo en ligne (5.6.107, lot H ; 5.6.108, lot D), à lancer après l'installation, depuis n'importe quel poste :

    python3 test-fumee.py https://demo.example.test                 parcours complet (crée puis efface UNE instance)
    python3 test-fumee.py https://demo.example.test --sans-instance  seulement accueil, en-têtes et refus (ne crée rien)

Vérifie : page d'accueil et mention vie privée (quatre langues), en-têtes de sécurité (dont ``Referrer-Policy: same-origin``), refus
des chemins d'administration (404 du répartiteur, ou 403 d'Apache pour ``/server-status``), retour à l'accueil (303) d'une adresse
sans session, y compris sous ``/agent-courriel`` (sinon : une autre installation d'AxiorHub sur le même serveur répond à la place de
la démo), cookie inventé sans effet, démarrage refusé sans origine ou depuis un autre site, démarrage comme le fait un navigateur
(``Origin: null`` et ``Sec-Fetch-Site: same-origin``), cookie HttpOnly, Secure, SameSite=Lax, ouverture de la session de l'instance,
page de session, fin et effacement ; jamais de fenêtre de mot de passe (``WWW-Authenticate``).
Python standard seulement. Code de sortie 0 si tout est bon, 1 sinon.
"""
import argparse
import http.client
import re
import sys
import time
from urllib.parse import urlencode, urlsplit

ENTETES_ATTENDUES = ('strict-transport-security', 'x-content-type-options', 'x-frame-options', 'referrer-policy')
ADMIN = ('/administration', '/installation', '/comptes', '/api/v1/cabinet', '/mcp', '/integration/v1/pair', '/server-status',
         '/api440/m5661/appliquer', '/ADMINISTRATION', '/_demo/admin', '/agent-courriel/administration')
APACHE_403 = ('/server-status',)            # Apache peut refuser lui-même ces adresses (403) avant le répartiteur : résultat voulu
# 5.6.108 : bouton « Commencer » de l'accueil dans chaque langue (?langue=…)
BOUTONS = {'fr': 'Commencer la démo', 'en': 'Start the demo', 'de': 'Demo starten', 'es': 'Iniciar la demo'}
# En-têtes qu'envoie Chrome au POST du formulaire « Commencer » quand la page applique « Referrer-Policy: no-referrer »
NAVIGATEUR = {'Origin': 'null', 'Sec-Fetch-Site': 'same-origin', 'Sec-Fetch-Mode': 'navigate', 'Sec-Fetch-Dest': 'document',
              'Sec-Fetch-User': '?1', 'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8'}
AIDE_LOCATION = ('une autre installation d’AxiorHub sur ce serveur répond à /agent-courriel à la place de la démo : ajoutez le bloc '
                 '<Location /agent-courriel> à l’hôte virtuel de la démo (LISEZMOI.md, section « Apache »)')


class Client:
    def __init__(self, base, delai=30, origine=None):
        u = urlsplit(base)
        self.origine = origine or '%s://%s' % (u.scheme, u.netloc)
        self.https, self.hote, self.port = u.scheme == 'https', u.hostname, u.port
        self.delai = delai
        self.basic = []                      # adresses qui ont demandé une fenêtre de mot de passe (WWW-Authenticate)

    def appel(self, methode, chemin, cookies=None, corps=None, origine=True, entetes_sup=None):
        classe = http.client.HTTPSConnection if self.https else http.client.HTTPConnection
        c = classe(self.hote, self.port, timeout=self.delai)
        entetes = {'User-Agent': 'axiorhub-demo-test-fumee', 'Accept-Language': 'fr-FR,fr;q=0.9'}
        if cookies:
            entetes['Cookie'] = '; '.join('%s=%s' % kv for kv in cookies.items())
        if corps is not None:
            corps = urlencode(corps).encode()
            entetes['Content-Type'] = 'application/x-www-form-urlencoded'
            if origine:
                entetes['Origin'] = self.origine
        entetes.update(entetes_sup or {})
        try:
            c.request(methode, chemin, body=corps, headers=entetes)
            r = c.getresponse()
            recus = [(k.lower(), v) for k, v in r.getheaders()]
            if any(k == 'www-authenticate' for k, _ in recus):
                self.basic.append(chemin.split('?')[0])
            return r.status, recus, r.read().decode('utf-8', 'replace')
        finally:
            c.close()


def cookie(entetes, nom):
    for k, v in entetes:
        if k == 'set-cookie' and v.split('=', 1)[0] == nom:
            return v.split(';', 1)[0].split('=', 1)[1], [x.strip().lower() for x in v.split(';')[1:]]
    return None, []


def lieu_accueil(entetes, origine):
    return dict(entetes).get('location', '') in ('/', origine + '/')


def parcours(base, sans_instance=False, sortie=print, attente=120, origine=None):
    cl, echecs = Client(base, origine=origine), []

    def verifier(condition, libelle):
        sortie(('OK      ' if condition else 'ÉCHEC   ') + libelle)
        if not condition:
            echecs.append(libelle)
        return condition

    statut, entetes, page = cl.appel('GET', '/')
    verifier(statut == 200 and 'Commencer la démo' in page, "page d'accueil")
    verifier('Données fictives' in page and 'aucun compte' in page, 'mention vie privée (données fictives, aucun compte)')
    noms = {k for k, _ in entetes}
    verifier(all(n in noms for n in ENTETES_ATTENDUES), 'en-têtes de sécurité (%s)' % ', '.join(ENTETES_ATTENDUES))
    verifier('content-security-policy' in noms and dict(entetes).get('cache-control', '') == 'private, no-store', 'CSP et no-store')
    politique = dict(entetes).get('referrer-policy', '')
    verifier(politique == 'same-origin', 'Referrer-Policy: same-origin (reçu : « %s » ; « no-referrer » fait refuser le bouton '
             '« Commencer » par Chrome)' % politique)
    pre, drapeaux = cookie(entetes, '__Host-axhdemo-pre')
    verifier(pre and {'secure', 'httponly', 'samesite=lax'} <= set(drapeaux), 'jeton du formulaire (cookie Secure, HttpOnly)')
    for langue, bouton in BOUTONS.items():
        statut, _, page = cl.appel('GET', '/?langue=' + langue)
        verifier(statut == 200 and bouton in page and '<html lang="%s"' % langue in page, 'accueil en %s (%s)' % (langue, bouton))
    codes = {p: cl.appel('GET', p)[0] for p in ADMIN}
    mauvais = {p: c for p, c in codes.items() if not (c == 404 or (c == 403 and p in APACHE_403))}
    verifier(not mauvais, "chemins d'administration refusés (404 ; 403 d'Apache admis pour %s)%s"
             % (', '.join(APACHE_403), (' — reçus : %s' % mauvais) if mauvais else ''))
    statut, entetes, _ = cl.appel('GET', '/code/essai-fumee')
    verifier(statut == 303 and lieu_accueil(entetes, cl.origine), 'adresse sans session : retour à l’accueil (303, code %d)' % statut)
    statut, entetes, _ = cl.appel('GET', '/agent-courriel/aujourdhui')
    if statut == 303 and lieu_accueil(entetes, cl.origine):
        verifier(True, '/agent-courriel/… sans session : 303 du répartiteur de la démo')
    else:
        verifier(False, '/agent-courriel/… sans session : code %d au lieu du 303 du répartiteur — %s' % (statut, AIDE_LOCATION))
    statut, _, page = cl.appel('GET', '/', cookies={'__Host-axhdemo': 'A' * 43})
    verifier(statut == 200 and 'terminée' in page, 'cookie inventé : aucune instance')
    statut, _, _ = cl.appel('POST', '/_demo/commencer', cookies={'__Host-axhdemo-pre': pre or ''}, corps={'pre': pre or ''},
                            origine=False)
    verifier(statut == 403, 'démarrage sans origine refusé')
    statut, _, _ = cl.appel('POST', '/_demo/commencer', cookies={'__Host-axhdemo-pre': pre or ''}, corps={'pre': pre or ''},
                            origine=False, entetes_sup={**NAVIGATEUR, 'Sec-Fetch-Site': 'cross-site'})
    verifier(statut == 403, 'démarrage depuis un autre site refusé (Origin: null, Sec-Fetch-Site: cross-site)')
    if sans_instance or echecs:
        return fin(cl, echecs, verifier)
    statut, entetes, _ = cl.appel('POST', '/_demo/commencer', cookies={'__Host-axhdemo-pre': pre}, corps={'pre': pre, 'langue': 'fr'},
                                  origine=False, entetes_sup=NAVIGATEUR)
    jeton, drapeaux = cookie(entetes, '__Host-axhdemo')
    if not verifier(statut in (303, 503, 429) and (statut != 303 or jeton),
                    'démarrage comme un navigateur (Origin: null, Sec-Fetch-Site: same-origin ; code %d)' % statut):
        return fin(cl, echecs, verifier)
    if statut != 303:
        sortie('INFO    démo complète ou limite atteinte : parcours arrêté sans erreur de configuration')
        return fin(cl, echecs, verifier)
    verifier({'path=/', 'secure', 'httponly', 'samesite=lax'} <= set(drapeaux), 'cookie de session HttpOnly, Secure, SameSite=Lax')
    session = {'__Host-axhdemo': jeton}
    limite = time.time() + attente
    while True:
        statut, entetes, page = cl.appel('GET', '/', cookies=session)
        if 'Préparation de votre démonstration' not in page or time.time() > limite:
            break
        time.sleep(2)
    lieu = dict(entetes).get('location', '')
    verifier(statut == 303 and '/poste-session?' in lieu, "ouverture de la session de démonstration (code %d)" % statut)
    for _ in range(4):                       # jeton de session, puis page d'arrivée (adresses du même domaine seulement)
        if statut not in (302, 303) or not lieu.startswith('/') or lieu.startswith('//'):
            break
        statut, entetes, page = cl.appel('GET', lieu, cookies=session)
        for k, v in entetes:
            if k == 'set-cookie' and not v.startswith('__Host-axhdemo'):
                nom, _, valeur = v.split(';', 1)[0].partition('=')
                session[nom] = valeur
        lieu = dict(entetes).get('location', '')
    verifier(statut == 200 and 'Commencer la démo' not in page and 'Préparation de votre démonstration' not in page
             and 'session de démonstration a été perdue' not in page, "page de l'instance (code %d)" % statut)
    verifier(cl.appel('GET', '/administration', cookies=session)[0] == 404, "administration refusée avec une session")
    statut, _, page = cl.appel('GET', '/_demo/session', cookies=session)
    csrf = re.search(r'name="csrf" value="([^"]+)"', page)
    verifier(statut == 200 and csrf, 'page de session')
    statut, entetes, page = cl.appel('POST', '/_demo/terminer', cookies=session, corps={'csrf': csrf.group(1) if csrf else ''})
    verifier(statut == 200 and 'effacé' in page, 'fin de la démonstration et effacement')
    statut, _, page = cl.appel('GET', '/', cookies=session)
    verifier('terminée' in page, 'ancien cookie sans effet après la fin')
    return fin(cl, echecs, verifier)


def fin(cl, echecs, verifier):
    verifier(not cl.basic, 'aucune fenêtre de mot de passe (WWW-Authenticate)%s'
             % ((' — demandée par : %s ; %s' % (', '.join(sorted(set(cl.basic))), AIDE_LOCATION)) if cl.basic else ''))
    return echecs


def main(argv=None):
    p = argparse.ArgumentParser(description='Test fumée de la démo en ligne.')
    p.add_argument('adresse', help='par exemple https://demo.example.test')
    p.add_argument('--sans-instance', action='store_true')
    a = p.parse_args(argv)
    echecs = parcours(a.adresse.rstrip('/'), a.sans_instance)
    print('\n%s' % ('Tout est bon.' if not echecs else '%d vérification(s) en échec.' % len(echecs)))
    return 1 if echecs else 0


if __name__ == '__main__':
    sys.exit(main())
