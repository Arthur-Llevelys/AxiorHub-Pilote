"""5.6.109 (lot R) : essai reproductible de l'hypothèse d'un incident sur un serveur de production en 5.6.108 (base du bureau endommagée après la
sauvegarde).

Hypothèse : sous Linux, fermer N'IMPORTE QUEL descripteur d'un fichier annule TOUS les verrous POSIX (``fcntl``) que le processus détient sur ce
fichier (fcntl(2) ; SQLite, « How To Corrupt An SQLite Database File », § 2.2). ``sauvegarde5688._est_sqlite`` (jusqu'à la 5.6.108) ouvrait par
``open()`` puis refermait ``desk.sqlite3`` dans le processus du worker, qui garde des connexions SQLite ouvertes sur ce fichier. En mode WAL, chaque
connexion tient un verrou PARTAGÉ sur le fichier de base tant qu'elle est ouverte : c'est ce verrou qui dit aux autres processus « je suis encore
là ». S'il disparaît, le processus suivant qui ferme sa connexion (``manage.py run`` toutes les cinq minutes) se croit dernier, obtient le verrou
EXCLUSIF, fait le point de contrôle et supprime le journal WAL pendant que le worker continue d'écrire dans l'ancien.

L'essai, sans dépendance, sur une base temporaire fictive :
  A (ce processus) : connexion WAL ouverte, écritures ;
  sonde (processus à part) : ``fcntl(F_GETLK)`` sur la plage des verrous partagés de SQLite : un autre processus pourrait-il poser un verrou
  exclusif ? (un verrou POSIX ne se voit que d'un autre processus) ;
  A : ``open()`` / ``read(16)`` / ``close()`` du fichier, exactement comme l'ancien ``_est_sqlite`` ;
  sonde de nouveau ; B (processus à part) : ouvre, écrit, ferme ; A écrit encore ; puis contrôle (``integrity_check``, lignes perdues, journal WAL
  supprimé sous les pieds de A).
Le même déroulé sans l'``open()``/``close()`` sert de témoin.

Lancement (Linux uniquement ; sous Windows les verrous de fichiers ne suivent pas ce modèle et l'essai se déclare non applicable) :
  python3 tests/essai_verrous56109.py            (compte rendu JSON, code 0 si l'essai a pu se dérouler, 77 si non applicable)
  docker run --rm -v "<dossier du dépôt>:/src:ro" -w /src python:3.12 python3 tests/essai_verrous56109.py
"""
import json
import os
import sqlite3
import struct
import subprocess
import sys
import tempfile

PENDING_BYTE = 0x40000000           # plage des verrous de SQLite (os_unix.c)
SHARED_FIRST = PENDING_BYTE + 2
SHARED_SIZE = 510
LIGNES_INITIALES, TOURS, LIGNES_PAR_TOUR = 200, 6, 80

SONDE = r'''
import fcntl, json, os, struct, sys
fd = os.open(sys.argv[1], os.O_RDONLY)
try:
    demande = struct.pack('hhqqi4x', fcntl.F_WRLCK, os.SEEK_SET, int(sys.argv[2]), int(sys.argv[3]), 0)
    rendu = fcntl.fcntl(fd, fcntl.F_GETLK, demande)
    l_type, _, _, _, l_pid = struct.unpack('hhqqi4x', rendu)
    print(json.dumps({'conflit': l_type != fcntl.F_UNLCK, 'pid': l_pid}))
finally:
    os.close(fd)
'''

ECRIVAIN = r'''
import sqlite3, sys
db = sqlite3.connect(sys.argv[1], timeout=10, isolation_level=None)
db.execute('PRAGMA journal_mode=WAL')
db.execute("INSERT INTO t(source, donnee) VALUES ('B', 'ligne fictive DURAND')")
db.close()
'''


def _sonde(chemin):
    sortie = subprocess.run([sys.executable, '-c', SONDE, chemin, str(SHARED_FIRST), str(SHARED_SIZE)], capture_output=True, text=True, timeout=30)
    if sortie.returncode:
        return {'erreur': sortie.stderr.strip()[-300:]}
    return json.loads(sortie.stdout)


def _ecrire(db, n, source):
    for i in range(n):
        db.execute('INSERT INTO t(source, donnee) VALUES (?, ?)', (source, 'dossier fictif DOS-001 ligne %d ' % i + 'x' * 200))


def deroule(avec_open_close, dossier):
    """Un déroulé complet ; rend les constats (aucune interprétation)."""
    chemin = os.path.join(dossier, 'desk-%s.sqlite3' % ('essai' if avec_open_close else 'temoin'))
    a = sqlite3.connect(chemin, timeout=10, isolation_level=None)
    a.execute('PRAGMA journal_mode=WAL')
    a.execute('PRAGMA wal_autocheckpoint=0')          # le journal garde les pages écrites par A : ce qu'un point de contrôle étranger perdrait
    a.execute('CREATE TABLE t(id INTEGER PRIMARY KEY, source TEXT, donnee TEXT)')
    a.execute('BEGIN')
    _ecrire(a, LIGNES_INITIALES, 'A')
    a.execute('COMMIT')
    a.execute('SELECT COUNT(*) FROM t').fetchone()
    attendues = LIGNES_INITIALES
    constats = {'avec_open_close': avec_open_close, 'tours': []}
    for tour in range(TOURS):
        etape = {'sonde_avant': _sonde(chemin)}
        if avec_open_close:
            with open(chemin, 'rb') as f:              # exactement l'ancien sauvegarde5688._est_sqlite (lot R : hors service : c'est l'objet de l'essai)
                etape['entete_sqlite'] = f.read(16) == b'SQLite format 3\x00'   # lot R : hors service
        etape['sonde_apres'] = _sonde(chemin)
        etape['wal_avant_b'] = os.path.getsize(chemin + '-wal') if os.path.exists(chemin + '-wal') else None
        b = subprocess.run([sys.executable, '-c', ECRIVAIN, chemin], capture_output=True, text=True, timeout=60)
        etape['b_code'] = b.returncode
        if b.returncode == 0:
            attendues += 1
        else:
            etape['b_erreur'] = b.stderr.strip()[-300:]
        etape['wal_apres_b'] = os.path.getsize(chemin + '-wal') if os.path.exists(chemin + '-wal') else None
        try:
            a.execute('BEGIN')
            _ecrire(a, LIGNES_PAR_TOUR, 'A')
            a.execute('COMMIT')
            attendues += LIGNES_PAR_TOUR
            etape['a_voit'] = a.execute('SELECT COUNT(*) FROM t').fetchone()[0]
        except sqlite3.Error as erreur:
            etape['a_erreur'] = str(erreur)
            try:
                a.execute('ROLLBACK')
            except sqlite3.Error:
                pass
        constats['tours'].append(etape)
    a.close()
    c = sqlite3.connect(chemin, timeout=10)
    try:
        constats['integrity_check'] = [r[0] for r in c.execute('PRAGMA integrity_check(20)')]
        constats['lignes_relues'] = c.execute('SELECT COUNT(*) FROM t').fetchone()[0]
    except sqlite3.Error as erreur:
        constats['controle_erreur'] = str(erreur)
    finally:
        c.close()
    constats['lignes_attendues'] = attendues
    constats['verrou_partage_vu_avant'] = all(t['sonde_avant'].get('conflit') for t in constats['tours'][:1])
    constats['verrou_perdu_apres_open_close'] = any(not t['sonde_apres'].get('conflit', True) for t in constats['tours'])
    constats['wal_supprime_par_b'] = any(t['wal_avant_b'] is not None and t['wal_apres_b'] in (None, 0) for t in constats['tours'])
    constats['base_integre'] = constats.get('integrity_check') == ['ok']
    constats['lignes_perdues'] = attendues - constats.get('lignes_relues', 0) if 'lignes_relues' in constats else None
    return constats


def conclusion(essai, temoin):
    """Démontré : le verrou disparaît ET la base est abîmée ou des lignes validées sont perdues, le témoin restant sain.
    Plausible : le verrou disparaît (mécanisme établi) sans dommage observé dans ce déroulé. Écarté : le verrou ne disparaît pas."""
    sain = temoin['base_integre'] and not temoin['lignes_perdues'] and not temoin['verrou_perdu_apres_open_close']
    if not essai['verrou_perdu_apres_open_close']:
        return 'ecarte'
    if sain and (not essai['base_integre'] or essai['lignes_perdues'] or essai['wal_supprime_par_b']):
        return 'demontre'
    return 'plausible'


def essai():
    if os.name != 'posix' or sys.platform == 'darwin':
        return {'applicable': False, 'systeme': sys.platform,
                'motif': 'Verrous POSIX de fcntl absents (Windows) ou de sémantique différente : essai à lancer sous Linux.'}
    with tempfile.TemporaryDirectory(prefix='lotR-verrous-') as dossier:
        temoin = deroule(False, dossier)
        avec = deroule(True, dossier)
    return {'applicable': True, 'systeme': sys.platform, 'sqlite': sqlite3.sqlite_version, 'python': sys.version.split()[0],
            'temoin': temoin, 'essai': avec, 'conclusion': conclusion(avec, temoin)}


def main():
    rendu = essai()
    print(json.dumps(rendu, ensure_ascii=False, indent=1))
    return 0 if rendu['applicable'] else 77


if __name__ == '__main__':
    sys.exit(main())
