"""5.6.108 (lot S) : recette multi-postes de la synchronisation sans serveur, dans des conteneurs Docker sur un réseau privé simulé.

Hôte (orchestrateur) :  python scripts/essai_sync56108.py --hote [--image python:3.12]
  crée un réseau Docker ``axh-s108`` (plage privée choisie par Docker), lance trois conteneurs (postes A, B, C) qui exécutent ce même
  script en mode ``--poste``, et pilote les aléas depuis l'hôte : coupure réseau (déconnexion puis reconnexion), sortie de veille simulée
  (``docker pause`` / ``unpause``), changement d'adresse (reconnexion avec une autre adresse), gros fichier, même modification hors ligne
  des deux côtés, horloge décalée (simulée dans le processus : l'horloge ne décide de rien), poste retiré, dossier réservé.
Conteneur (poste) :   python scripts/essai_sync56108.py --poste A --rdv /rdv
  chaque poste a sa base (profil temporaire), écoute sur SON adresse privée du réseau Docker ; le rendez-vous (``/rdv``, volume partagé)
  ne sert qu'à passer le code d'appairage et les consignes de l'orchestrateur, comme un avocat le lirait à l'écran.

Données fictives (ZORVAL, KERVILAC, ZOR-7xx). Aucun appel hors du réseau Docker ; installation de ``cryptography`` dans le conteneur
seulement (autorisée par le cabinet pour les essais). Ce qui n'est pas vu sur de vrais PC (pare-feu Windows, Wi-Fi isolant, veille réelle)
reste à recetter sur place.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

RACINE = Path(__file__).resolve().parents[1]
RESEAU = 'axh-s108'
POSTES = ('A', 'B', 'C')


# ------------------------------------------------------------------------------------------------ côté conteneur
def _ecrire(rdv, nom, valeur):
    tmp = Path(rdv) / (nom + '.tmp')
    tmp.write_text(json.dumps(valeur), encoding='utf-8')
    os.replace(tmp, Path(rdv) / (nom + '.json'))


def _lire(rdv, nom, attente=180):
    fin = time.time() + attente
    while time.time() < fin:
        p = Path(rdv) / (nom + '.json')
        if p.is_file():
            try:
                return json.loads(p.read_text(encoding='utf-8'))
            except ValueError:
                pass
        time.sleep(0.3)
    raise SystemExit('rendez-vous absent : ' + nom)


def _desk(base):
    sys.path.insert(0, str(RACINE))
    from agent.desk import Desk
    state = Path(base) / 'state'
    state.mkdir(parents=True, exist_ok=True)
    matters = Path(base) / 'matters.json'
    matters.write_text('[]', encoding='utf-8')
    stockage = Path(base) / 'stockage'
    (stockage / 'Dossiers').mkdir(parents=True, exist_ok=True)
    c = {'mode': 'drafts', 'state_dir': str(state), 'matters_file': str(matters), 'nextcloud': {'roots': ['/Dossiers'], 'local_path': str(stockage)},
         'documents': {},
         'mail': {'host': 'mail.example.test', 'username': 'cabinet@example.test', 'own_addresses': ['cabinet@example.test'], 'inbox': 'INBOX',
                  'sent': 'Sent', 'drafts': 'Drafts', 'from_name': 'Cabinet', 'from_address': 'cabinet@example.test', 'signature': 'Cabinet'},
         'ollama': {'model': 'test-local'}, 'calendar': {'urls': [], 'timezone': 'Europe/Paris'}}
    d = Desk(c)
    from agent import membres5650
    d.db.execute(membres5650.SCHEMA)
    d.db.execute("INSERT OR REPLACE INTO membres5650(owner,name,role,first_seen,last_seen) VALUES('cabinet','Personne','administrateur','2026-11-18','2026-11-18')")
    d.db.commit()
    return c, d


def poste(nom, rdv):
    """Un poste : boucle de synchronisation réelle (``Liaison.synchroniser``) et consignes lues au rendez-vous."""
    base = tempfile.mkdtemp(prefix='axh-s108-')
    c, desk = _desk(base)
    from agent import liaison56103 as L, sync56108 as S, sync56108_objets as O, sync56108_fichiers as F, dossiers5697 as D
    from agent.acces5697 import Acteur
    from agent.desk import Desk
    admin = Acteur('cabinet', 'administrateur')
    personne = {'A': 'Me DURAND', 'B': 'Me MARTIN', 'C': 'Me BERNARD'}[nom]
    desk.setting(L.REGLAGE_NOM, 'Poste ' + nom)
    journal = []

    def note(*x):
        journal.append(' '.join(str(v) for v in x))
        print('[%s]' % nom, *x, flush=True)

    if nom == 'A':
        S.creer_espace(desk, 'Cabinet ZORVAL', personne, 'cabinet')
    else:
        S.ouvrir_rejoindre(desk, personne, 'cabinet')
    note('écoute requise', S.ecoute_requise(desk))
    liaison = None

    def ecouter():
        nonlocal liaison
        adresse = L.choisir_adresse(desk)
        liaison = L.Liaison(lambda: Desk(c)).demarrer(adresse, L.PORT_TCP, decouverte=True)
        note('à l’écoute sur', adresse)
        return adresse

    if nom == 'A':
        code = L.nouveau_code(desk, duree=600)['code']
        adresse = ecouter()
        _ecrire(rdv, 'A', {'adresse': adresse, 'port': L.PORT_TCP, 'code': code})
    else:
        ecouter()
        a = _lire(rdv, 'A')
        if nom == 'C':
            a = _lire(rdv, 'code-C')
        L.appairer(desk, a['adresse'], a['port'], a['code'])
        note('appairé avec A')
        if nom == 'B':
            _ecrire(rdv, 'B-appaire', {})
    etapes_faites = set()
    debut = time.time()
    while time.time() - debut < 900:
        try:
            liaison.synchroniser(forcer=True)
            liaison.annoncer()
        except Exception as ex:   # noqa: BLE001 — coupure : l'essai suivant reprend
            note('échange interrompu', type(ex).__name__)
        if nom == 'A':
            for r in desk.db.execute("SELECT poste FROM sync56108_adhesions WHERE etat='en_attente'").fetchall():
                S.accepter_adhesion(desk, r[0], 'cabinet')
                note('adhésion acceptée')
        consigne = Path(rdv) / ('ordre-%s.json' % nom)
        if consigne.is_file():
            ordre = json.loads(consigne.read_text(encoding='utf-8'))
            consigne.unlink()
            note('ordre', ordre['faire'])
            try:
                D.ensure_schema(desk)
                resultat = executer(nom, ordre, desk, c, rdv, S, O, F, D, L, admin)
            except Exception as ex:   # noqa: BLE001 — l'erreur est rendue à l'orchestrateur, le poste continue
                note('erreur', repr(ex))
                resultat = {'erreur': repr(ex), 'espace': False, 'dossiers': [], 'documents': [], 'conflits': 0, 'reserves': []}
            _ecrire(rdv, 'fait-%s-%s' % (nom, ordre['faire']), resultat)
            etapes_faites.add(ordre['faire'])
            if ordre['faire'] == 'fin':
                break
        time.sleep(1)
    liaison.arreter()
    return 0


def executer(nom, ordre, desk, c, rdv, S, O, F, D, L, admin):
    f = ordre['faire']
    if f == 'etat':
        D.traiter_rangements(desk)
        racine = Path(c['nextcloud']['local_path'])
        from agent.common import Stop as _Stop
        try:
            from agent.sync56108_missions import garde_travail
            garde_travail(desk, 'routine520', {'kind': 'tri'})
            tri = True
        except _Stop:
            tri = False
        return {'fichiers_dossier': sorted(str(x.relative_to(racine)) for x in racine.rglob('*') if x.is_file()), 'tri_ici': tri,
                'documents_detail': [dict(r) for r in desk.db.execute('SELECT nom, etat, dossier_chemin FROM sync56108_documents')], **{'espace': bool(S.espace_cabinet(desk)), 'dossiers': [r[0] for r in desk.db.execute('SELECT titre FROM dossiers5697 ORDER BY titre')],
                'documents': [dict(r) for r in desk.db.execute('SELECT nom, etat, sha256, taille FROM sync56108_documents')],
                'conflits': desk.db.execute('SELECT COUNT(*) FROM sync56108_objets WHERE conflit=1').fetchone()[0],
                'reserves': [r[0] for r in desk.db.execute("SELECT titre FROM dossiers5697 WHERE confidentialite='reserve'")]}}
    if f == 'dossier':
        lid = D.creer(desk, admin, {'titre': ordre['titre'], 'reference': ordre['ref'], 'client': 'ZORVAL'}, 'cle-essai-' + ordre['ref'])['id']
        S.partager_dossier(desk, lid)
        return {'local': lid, 'objet': O.objet_de(desk, 'dossier', lid)['objet']}
    if f == 'reserve':
        lid = D.creer(desk, admin, {'titre': ordre['titre'], 'reference': ordre['ref'], 'client': 'KERVILAC', 'confidentialite': 'reserve',
                                    'membres': ['cabinet']}, 'cle-essai-' + ordre['ref'])['id']
        gid = desk.db.execute("SELECT gid FROM sync56108_personnes WHERE nom=?", (ordre['personne'],)).fetchone()[0]
        S.partager_dossier(desk, lid, [gid])
        return {'local': lid}
    if f == 'fichier':
        chemin = Path(c['state_dir']) / 'gros.bin'
        with open(chemin, 'wb') as fh:
            graine = 7
            for _ in range(ordre['mio']):
                graine = (graine * 1103515245 + 12345) % (2 ** 31)
                fh.write(graine.to_bytes(4, 'big') * (262144))
        dg = desk.db.execute("SELECT objet FROM sync56108_objets WHERE genre='dossier' LIMIT 1").fetchone()[0]
        return F.publier(desk, str(chemin), 'Pièces ZORVAL.pdf', dg)
    if f == 'modifier':
        lid = desk.db.execute("SELECT local_id FROM sync56108_objets WHERE genre='dossier' AND local_id IS NOT NULL ORDER BY maj_le LIMIT 1").fetchone()[0]
        v = D.lire(desk, admin, lid)['version']
        if ordre.get('horloge_decalee'):
            from unittest.mock import patch
            with patch.object(S, '_maintenant', lambda: '2001-01-01T00:00:00+00:00'):
                D.modifier(desk, admin, lid, v, {'titre': ordre['titre']}, 'cle-essai-m-' + nom + str(time.time_ns()))
                S.journaliser(desk)
        else:
            D.modifier(desk, admin, lid, v, {'titre': ordre['titre']}, 'cle-essai-m-' + nom + str(time.time_ns()))
        return {}
    if f == 'fichier_dossier':
        D.traiter_rangements(desk)
        lid = desk.db.execute("SELECT local_id FROM sync56108_objets WHERE genre='dossier' AND local_id IS NOT NULL ORDER BY maj_le LIMIT 1").fetchone()[0]
        chemin = D.lire(desk, admin, lid)['chemin']
        cible = Path(c['nextcloud']['local_path']) / chemin.lstrip('/') / ordre['nom']
        cible.write_bytes(ordre['texte'].encode('utf-8'))
        desk.db.execute('UPDATE sync56108_fichiers_releve SET dernier=0')
        desk.db.commit()
        return F.relever(desk)
    if f == 'attribuer_tri':
        pid = desk.db.execute("SELECT poste FROM sync56108_membres WHERE nom=? AND retire=0", ('Poste ' + ordre['poste'],)).fetchone()[0]
        from agent.sync56108_missions import attribuer
        return attribuer(desk, 'Tri matinal des courriels', pid, routine='routine520:tri')
    if f == 'code':
        return {'code': L.nouveau_code(desk, duree=600)['code'], 'adresse': L.choisir_adresse(desk), 'port': L.PORT_TCP}
    if f == 'retirer':
        pid = desk.db.execute("SELECT poste FROM sync56108_membres WHERE nom=? AND retire=0", ('Poste ' + ordre['poste'],)).fetchone()[0]
        S.retirer_poste(desk, pid, 'cabinet')
        return {}
    if f == 'adresse':
        return {'adresse': L.choisir_adresse(desk)}
    return {}


# ------------------------------------------------------------------------------------------------ côté hôte
def _docker(*args, check=True):
    env = dict(os.environ, MSYS_NO_PATHCONV='1')
    return subprocess.run(['docker', *args], check=check, capture_output=True, text=True, env=env)


def hote(image):
    rdv = Path(tempfile.mkdtemp(prefix='axh-s108-rdv-'))
    _docker('network', 'rm', RESEAU, check=False)
    _docker('network', 'create', RESEAU)
    noms = {p: 'axh-s108-' + p.lower() for p in POSTES}
    resultats = []

    def verdict(libelle, ok, detail=''):
        resultats.append({'essai': libelle, 'reussi': bool(ok), 'detail': detail})
        print(('RÉUSSI ' if ok else 'ÉCHEC  ') + libelle + (' — ' + str(detail) if detail else ''), flush=True)

    def ordre(p, faire, attente=240, **kw):
        tmp = rdv / ('ordre-%s.tmp' % p)
        tmp.write_text(json.dumps({'faire': faire, **kw}), encoding='utf-8')
        os.replace(tmp, rdv / ('ordre-%s.json' % p))
        cible = rdv / ('fait-%s-%s.json' % (p, faire))
        fin = time.time() + attente
        while time.time() < fin:
            if cible.is_file():
                out = json.loads(cible.read_text(encoding='utf-8'))
                cible.unlink()
                return out
            time.sleep(0.5)
        raise RuntimeError('pas de réponse de %s à %s' % (p, faire))

    def attendre(p, test, attente=120):
        fin = time.time() + attente
        e = None
        while time.time() < fin:
            e = ordre(p, 'etat')
            if test(e):
                return e
            time.sleep(2)
        return e

    def lancer(p):
        src = str(RACINE).replace('\\', '/')
        _docker('run', '-d', '--name', noms[p], '--network', RESEAU, '-v', src + ':/src:ro', '-v', str(rdv).replace('\\', '/') + ':/rdv',
                '-w', '/src', '-e', 'PYTHONPATH=/src', '-e', 'PYTHONUTF8=1', '-e', 'PYTHONDONTWRITEBYTECODE=1', image, 'sh', '-c',
                'pip install -q cryptography waitress >/dev/null 2>&1; python scripts/essai_sync56108.py --poste %s --rdv /rdv' % p)

    try:
        for p in POSTES:
            _docker('rm', '-f', noms[p], check=False)
        lancer('A')
        lancer('B')
        e = attendre('B', lambda e: e['espace'], 240)
        verdict('deux postes : B a rejoint l’espace de A (code, adhésion acceptée)', e and e['espace'])
        ordre('A', 'dossier', titre='Bail commercial ZORVAL', ref='ZOR-701')
        e = attendre('B', lambda e: 'Bail commercial ZORVAL' in e['dossiers'])
        verdict('dossier partagé reçu sur B', 'Bail commercial ZORVAL' in e['dossiers'])
        # coupure réseau pendant un gros fichier, puis reprise
        ordre('A', 'fichier', mio=24)
        time.sleep(3)
        _docker('network', 'disconnect', RESEAU, noms['B'])
        time.sleep(8)
        _docker('network', 'connect', RESEAU, noms['B'])
        e = attendre('B', lambda e: any(d['etat'] == 'disponible' for d in e['documents']), 300)
        verdict('gros fichier (24 Mio) après coupure : disponible, empreinte contrôlée', any(d['etat'] == 'disponible' for d in e['documents']),
                [d['etat'] for d in e['documents']])
        # S5 : fichier ajouté au répertoire du dossier sur A (hors AxiorHub) → déposé dans le répertoire du même dossier sur B
        ordre('B', 'etat')                                        # rangement du dossier reçu (répertoire local de B)
        ordre('A', 'fichier_dossier', nom='Note ZORVAL.txt', texte='note fictive pour le dossier ZORVAL')
        e = attendre('B', lambda e: any(f.endswith('Note ZORVAL.txt') for f in e['fichiers_dossier']), 180)
        verifier = [f for f in e['fichiers_dossier'] if f.endswith('Note ZORVAL.txt')]
        verdict('fichier du dossier publié par A, déposé dans le répertoire du dossier sur B', bool(verifier), verifier or e.get('documents_detail'))
        # S5 : une seule exécution d'une routine présente sur les deux postes
        ordre('A', 'attribuer_tri', poste='B')
        eb = attendre('B', lambda e: e['tri_ici'] is True)
        ea = ordre('A', 'etat')
        verdict('routine partagée : exécutée par B seulement (A n’exécute pas)', eb['tri_ici'] is True and ea['tri_ici'] is False)
        # sortie de veille simulée
        _docker('pause', noms['B'])
        ordre('A', 'modifier', titre='Bail commercial ZORVAL (avenant)')
        time.sleep(10)
        _docker('unpause', noms['B'])
        e = attendre('B', lambda e: 'Bail commercial ZORVAL (avenant)' in e['dossiers'])
        verdict('sortie de veille : modification reçue au réveil', 'Bail commercial ZORVAL (avenant)' in e['dossiers'])
        # même modification hors ligne des deux côtés (horloge de A décalée : sans effet sur la décision)
        _docker('network', 'disconnect', RESEAU, noms['B'])
        ordre('A', 'modifier', titre='Version de A', horloge_decalee=True)
        ordre('B', 'modifier', titre='Version de B')
        _docker('network', 'connect', RESEAU, noms['B'])
        ea = attendre('A', lambda e: e['conflits'] >= 1)
        eb = attendre('B', lambda e: e['conflits'] >= 1)
        verdict('même modification hors ligne : conflit conservé des deux côtés, aucune version choisie par l’horloge',
                ea['conflits'] >= 1 and eb['conflits'] >= 1 and 'Version de A' in ea['dossiers'] and 'Version de B' in eb['dossiers'])
        # changement d'adresse de B (DHCP) : reconnexion avec une autre adresse
        avant = ordre('B', 'adresse')['adresse']
        _docker('network', 'disconnect', RESEAU, noms['B'])
        _docker('network', 'connect', RESEAU, noms['B'])
        apres = ordre('B', 'adresse')['adresse']
        ordre('A', 'dossier', titre='Succession KERVILAC', ref='ZOR-702')
        e = attendre('B', lambda e: 'Succession KERVILAC' in e['dossiers'], 180)
        verdict('changement d’adresse (%s) : échanges repris' % ('adresse changée' if avant != apres else 'même adresse rendue par Docker'),
                'Succession KERVILAC' in e['dossiers'])
        # trois postes, dossier réservé, poste retiré
        code = ordre('A', 'code')
        _ecrire(str(rdv), 'code-C', code)
        lancer('C')
        e = attendre('C', lambda e: e['espace'] and 'Succession KERVILAC' in e['dossiers'], 300)
        verdict('trois postes : C a rejoint et reçu l’historique', e and 'Succession KERVILAC' in e['dossiers'])
        ordre('A', 'reserve', titre='Affaire réservée ORLANDEC', ref='ZOR-703', personne='Me MARTIN')
        eb = attendre('B', lambda e: 'Affaire réservée ORLANDEC' in e['reserves'])
        time.sleep(10)
        ec = ordre('C', 'etat')
        verdict('dossier réservé : reçu par B (autorisée), jamais par C', 'Affaire réservée ORLANDEC' in eb['reserves']
                and 'Affaire réservée ORLANDEC' not in ec['dossiers'])
        ordre('A', 'retirer', poste='C')
        ordre('A', 'dossier', titre='Après retrait de C', ref='ZOR-704')
        attendre('B', lambda e: 'Après retrait de C' in e['dossiers'])
        time.sleep(10)
        ec = ordre('C', 'etat')
        verdict('poste retiré : plus aucune réception', 'Après retrait de C' not in ec['dossiers'])
    except Exception as ex:   # noqa: BLE001
        verdict('déroulé de la recette', False, repr(ex))
    finally:
        for p in POSTES:
            try:
                ordre(p, 'fin', attente=5)
            except Exception:   # noqa: BLE001
                pass
            journal = _docker('logs', noms[p], check=False).stdout
            (rdv / ('journal-%s.txt' % p)).write_text(journal, encoding='utf-8')
            _docker('rm', '-f', noms[p], check=False)
        _docker('network', 'rm', RESEAU, check=False)
    (rdv / 'resultats.json').write_text(json.dumps(resultats, ensure_ascii=False, indent=1), encoding='utf-8')
    print('Résultats et journaux :', rdv)
    return 0 if resultats and all(r['reussi'] for r in resultats) else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--hote', action='store_true')
    ap.add_argument('--poste', choices=POSTES)
    ap.add_argument('--rdv', default='/rdv')
    ap.add_argument('--image', default='python:3.12')
    a = ap.parse_args()
    if a.poste:
        return poste(a.poste, a.rdv)
    if a.hote:
        if not shutil.which('docker'):
            print('Docker absent : recette impossible sur ce poste.')
            return 2
        return hote(a.image)
    ap.print_help()
    return 2


if __name__ == '__main__':
    sys.exit(main())
