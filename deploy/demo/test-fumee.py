#!/usr/bin/env python3
"""Test « fumée » de la démo en ligne (5.6.107, lot H), à lancer après l'installation, depuis n'importe quel poste :

    python3 test-fumee.py https://demo.example.test                 parcours complet (crée puis efface UNE instance)
    python3 test-fumee.py https://demo.example.test --sans-instance  seulement accueil, en-têtes et refus (ne crée rien)

Vérifie : page d'accueil et mention vie privée, en-têtes de sécurité, refus des chemins d'administration, cookie inventé sans effet,
démarrage d'une instance (cookie HttpOnly, Secure, SameSite=Lax), réponse de l'instance, page de session, fin et effacement.
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
         '/api440/m5661/appliquer', '/ADMINISTRATION', '/_demo/admin')


class Client:
    def __init__(self, base, delai=30, origine=None):
        u = urlsplit(base)
        self.origine = origine or '%s://%s' % (u.scheme, u.netloc)
        self.https, self.hote, self.port = u.scheme == 'https', u.hostname, u.port
        self.delai = delai

    def appel(self, methode, chemin, cookies=None, corps=None, origine=True):
        classe = http.client.HTTPSConnection if self.https else http.client.HTTPConnection
        c = classe(self.hote, self.port, timeout=self.delai)
        entetes = {'User-Agent': 'axiorhub-demo-test-fumee'}
        if cookies:
            entetes['Cookie'] = '; '.join('%s=%s' % kv for kv in cookies.items())
        if corps is not None:
            corps = urlencode(corps).encode()
            entetes['Content-Type'] = 'application/x-www-form-urlencoded'
            if origine:
                entetes['Origin'] = self.origine
        try:
            c.request(methode, chemin, body=corps, headers=entetes)
            r = c.getresponse()
            return r.status, [(k.lower(), v) for k, v in r.getheaders()], r.read().decode('utf-8', 'replace')
        finally:
            c.close()


def cookie(entetes, nom):
    for k, v in entetes:
        if k == 'set-cookie' and v.split('=', 1)[0] == nom:
            return v.split(';', 1)[0].split('=', 1)[1], [x.strip().lower() for x in v.split(';')[1:]]
    return None, []


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
    pre, drapeaux = cookie(entetes, '__Host-axhdemo-pre')
    verifier(pre and {'secure', 'httponly', 'samesite=lax'} <= set(drapeaux), 'jeton du formulaire (cookie Secure, HttpOnly)')
    verifier(all(cl.appel('GET', p)[0] == 404 for p in ADMIN), "chemins d'administration refusés (404)")
    statut, _, page = cl.appel('GET', '/', cookies={'__Host-axhdemo': 'A' * 43})
    verifier(statut == 200 and 'terminée' in page, 'cookie inventé : aucune instance')
    statut, _, _ = cl.appel('POST', '/_demo/commencer', cookies={'__Host-axhdemo-pre': pre or ''}, corps={'pre': pre or ''},
                            origine=False)
    verifier(statut == 403, 'démarrage sans origine refusé')
    if sans_instance or echecs:
        return echecs
    statut, entetes, _ = cl.appel('POST', '/_demo/commencer', cookies={'__Host-axhdemo-pre': pre}, corps={'pre': pre})
    jeton, drapeaux = cookie(entetes, '__Host-axhdemo')
    if not verifier(statut in (303, 503, 429) and (statut != 303 or jeton), 'démarrage (code %d)' % statut):
        return echecs
    if statut != 303:
        sortie('INFO    démo complète ou limite atteinte : parcours arrêté sans erreur de configuration')
        return echecs
    verifier({'path=/', 'secure', 'httponly', 'samesite=lax'} <= set(drapeaux), 'cookie de session HttpOnly, Secure, SameSite=Lax')
    session = {'__Host-axhdemo': jeton}
    fin = time.time() + attente
    while True:
        statut, entetes, page = cl.appel('GET', '/', cookies=session)
        if 'Préparation de votre démonstration' not in page or time.time() > fin:
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
    verifier(statut == 200 and 'Commencer la démo' not in page and 'Préparation' not in page, "page de l'instance (code %d)" % statut)
    verifier(cl.appel('GET', '/administration', cookies=session)[0] == 404, "administration refusée avec une session")
    statut, _, page = cl.appel('GET', '/_demo/session', cookies=session)
    csrf = re.search(r'name="csrf" value="([^"]+)"', page)
    verifier(statut == 200 and csrf, 'page de session')
    statut, entetes, page = cl.appel('POST', '/_demo/terminer', cookies=session, corps={'csrf': csrf.group(1) if csrf else ''})
    verifier(statut == 200 and 'effacé' in page, 'fin de la démonstration et effacement')
    statut, _, page = cl.appel('GET', '/', cookies=session)
    verifier('terminée' in page, 'ancien cookie sans effet après la fin')
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
