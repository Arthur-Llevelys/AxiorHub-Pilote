#!/usr/bin/env python3
"""5.6.110 (lot D) : copie réduite du moteur de dictée whisper.cpp pour les paquets du poste (binaires hors dépôt).

  python scripts/preparer-dictee.py ARCHIVES SORTIE

``ARCHIVES`` contient les deux archives officielles de la publication ``b5907`` de whisper.cpp, téléchargées et vérifiées à part
(ce script ne télécharge rien) :

- ``whisper-bin-x64.zip`` (Windows, tout est rangé sous ``Release/``) ;
- ``whisper-bin-ubuntu-x64.tar.gz`` (Linux, tout est rangé sous ``whisper-bin-ubuntu-x64/``).

Une archive dont la taille ou l'empreinte SHA-256 diffère de celle attendue est refusée. Seuls les fichiers utiles à la dictée sont
repris (noms fixés à l'avance, aucun chemin sortant, aucun lien symbolique écrit) :

- ``SORTIE/windows/`` : ``whisper-cli.exe``, ``whisper.dll``, ``ggml.dll``, ``ggml-base.dll`` et les neuf ``ggml-cpu-*.dll`` (choisies
  à l'exécution selon le processeur) ;
- ``SORTIE/linux/`` : ``whisper-cli``, ``libwhisper.so.1``, ``libggml.so.1``, ``libggml-base.so.1`` (copies des fichiers versionnés de
  l'archive, écrites sous le nom attendu par le chargeur : les liens symboliques de l'archive sont résolus) et les quatorze
  ``libggml-cpu-*.so``.

Chaque dossier reçoit aussi ``LICENSE-whisper.cpp.txt`` (le ``LICENSE`` de l'archive Linux ; l'archive Windows n'en contient pas),
``THIRD_PARTY_NOTICES-DICTEE.txt`` et ``MANIFEST-dictee.json`` (taille et SHA-256 de chaque fichier). Les binaires ne sont pas modifiés :
simple copie. Deux préparations des mêmes archives donnent des fichiers identiques à l'octet près. Rien n'est exécuté.
"""
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import sys
import tarfile
import zipfile

COMPOSANT = 'whisper.cpp'
VERSION = 'b5907'
COMMIT = '5eaee0cc9024eb9e9a99469199920c8b7f5cb9ec'
PROJET = 'https://github.com/ggml-org/whisper.cpp'
PUBLICATION = PROJET + '/releases/tag/' + VERSION
LICENCE = 'MIT'
VC_REDIST = 'https://aka.ms/vs/17/release/vc_redist.x64.exe'
# Modèles : jamais dans les paquets, téléchargés à la demande par l'administrateur (agent/dictee56110.py)
MODELES_DEPOT = 'https://huggingface.co/ggerganov/whisper.cpp'
MODELES_REVISION = '5359861c739e955e79d9a303bcbc70fb988958b1'

ARCHIVES = {
    'windows': {'nom': 'whisper-bin-x64.zip', 'octets': 9168428,
                'sha256': 'ecca07b277d943e9b9f4712856a144b857271a6b333104e511e1988e80db582b', 'prefixe': 'Release/'},
    'linux': {'nom': 'whisper-bin-ubuntu-x64.tar.gz', 'octets': 10413708,
              'sha256': 'a6199027fe5b4239c84171d2bca4c0ff44ec86a515494d5a39b237b212d429bd', 'prefixe': 'whisper-bin-ubuntu-x64/'},
}
CPU_WINDOWS = ('alderlake', 'cannonlake', 'cascadelake', 'haswell', 'icelake', 'sandybridge', 'skylakex', 'sse42', 'x64')
CPU_LINUX = ('alderlake', 'cannonlake', 'cascadelake', 'cooperlake', 'haswell', 'icelake', 'ivybridge', 'piledriver', 'sandybridge',
             'sapphirerapids', 'skylakex', 'sse42', 'x64', 'zen4')
BINAIRES = {
    'windows': ('whisper-cli.exe', 'whisper.dll', 'ggml.dll', 'ggml-base.dll') + tuple('ggml-cpu-%s.dll' % c for c in CPU_WINDOWS),
    'linux': ('whisper-cli', 'libwhisper.so.1', 'libggml.so.1', 'libggml-base.so.1') + tuple('libggml-cpu-%s.so' % c for c in CPU_LINUX),
}
EXECUTABLE = {'windows': 'whisper-cli.exe', 'linux': 'whisper-cli'}
LICENCE_FICHIER = 'LICENSE-whisper.cpp.txt'
NOTICE = 'THIRD_PARTY_NOTICES-DICTEE.txt'
MANIFESTE = 'MANIFEST-dictee.json'
TEXTES = (LICENCE_FICHIER, NOTICE)
DEPENDANCES = {
    'windows': ['Microsoft Visual C++ 2015-2022 (MSVCP140.dll, VCRUNTIME140.dll, VCRUNTIME140_1.dll, VCOMP140.DLL), non redistribué : '
                + VC_REDIST],
    'linux': ['libgomp1 (paquet Ubuntu)', 'glibc 2.34 ou plus récente'],
}
SAUTS_MAX = 4   # liens symboliques suivis au plus (libwhisper.so.1 -> libwhisper.so.1.9.6 : un seul saut)


class ArchiveRefusee(SystemExit):
    pass


def sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for bloc in iter(lambda: f.read(1 << 20), b''):
            h.update(bloc)
    return h.hexdigest()


def _nom_sur(nom):
    """Nom de membre acceptable : relatif, sans « .. », sans barre oblique inverse ni lecteur, sans caractère de contrôle."""
    if not nom or '\\' in nom or ':' in nom or nom.startswith('/') or any(ord(c) < 32 for c in nom):
        return False
    return '..' not in PurePosixPath(nom).parts


def verifier_archive(chemin, attendu):
    """Taille et SHA-256 de l'archive officielle ; refus sinon."""
    chemin = Path(chemin)
    if not chemin.is_file():
        raise ArchiveRefusee('archive absente : %s' % chemin)
    if chemin.stat().st_size != attendu['octets'] or sha256(chemin) != attendu['sha256']:
        raise ArchiveRefusee('empreinte inattendue, archive refusée : %s' % chemin.name)


def lire_windows(chemin, prefixe=ARCHIVES['windows']['prefixe']):
    """{nom: octets} des binaires Windows retenus, lus dans l'archive zip (un seul membre par nom, fichier ordinaire)."""
    out = {}
    with zipfile.ZipFile(chemin) as z:
        noms = [i.filename for i in z.infolist()]
        for n in noms:
            if not _nom_sur(n):
                raise ArchiveRefusee('chemin refusé dans l’archive : %r' % n)
        if len(set(noms)) != len(noms):
            raise ArchiveRefusee('noms en double dans l’archive Windows')
        infos = {i.filename: i for i in z.infolist()}
        for f in BINAIRES['windows']:
            info = infos.get(prefixe + f)
            if info is None or info.is_dir():
                raise ArchiveRefusee('fichier absent de l’archive Windows : ' + prefixe + f)
            if (info.external_attr >> 16) & 0o170000 == 0o120000:
                raise ArchiveRefusee('lien symbolique refusé dans l’archive Windows : ' + info.filename)
            out[f] = z.read(info)
    return out


def _resoudre(membres, prefixe, nom):
    """Membre ordinaire désigné par ``nom``, en suivant les liens symboliques de l'archive (même dossier, nom simple seulement)."""
    courant = nom
    for _ in range(SAUTS_MAX + 1):
        m = membres.get(prefixe + courant)
        if m is None:
            raise ArchiveRefusee('fichier absent de l’archive Linux : ' + prefixe + courant)
        if m.isreg():
            return m
        if not m.issym():
            raise ArchiveRefusee('membre refusé (ni fichier ni lien symbolique) : ' + m.name)
        cible = m.linkname
        if not cible or '/' in cible or '\\' in cible or cible in ('.', '..'):
            raise ArchiveRefusee('lien symbolique sortant refusé : %s -> %s' % (m.name, cible))
        courant = cible
    raise ArchiveRefusee('trop de liens symboliques enchaînés : ' + nom)


def lire_linux(chemin, prefixe=ARCHIVES['linux']['prefixe']):
    """({nom: octets} des binaires Linux retenus, octets du LICENSE). Les liens (libwhisper.so.1 -> libwhisper.so.1.9.6) sont lus
    comme des copies du fichier versionné, écrites sous le nom SONAME."""
    out = {}
    with tarfile.open(chemin, 'r:gz') as tar:
        membres = {}
        for m in tar.getmembers():
            if not _nom_sur(m.name):
                raise ArchiveRefusee('chemin refusé dans l’archive : %r' % m.name)
            if m.name in membres:
                raise ArchiveRefusee('noms en double dans l’archive Linux : ' + m.name)
            membres[m.name] = m
        for f in BINAIRES['linux'] + ('LICENSE',):
            m = _resoudre(membres, prefixe, f)
            out[f] = tar.extractfile(m).read()
    return {k: v for k, v in out.items() if k != 'LICENSE'}, out['LICENSE']


def copyright_de(licence):
    """Ligne « Copyright … » du texte de licence ; refus si absente (aucune mention inventée)."""
    for ligne in licence.decode('utf-8', 'replace').splitlines():
        if ligne.strip().lower().startswith('copyright'):
            return ligne.strip()
    raise ArchiveRefusee('mention de copyright introuvable dans LICENSE')


def avis(plateforme, archive, copyright_):
    """Texte de THIRD_PARTY_NOTICES-DICTEE.txt (une seule plateforme)."""
    dossier = 'dictee' if plateforme == 'windows' else '/usr/lib/axiorhub-pilote/dictee'
    lignes = ['AVIS DES COMPOSANTS TIERS — DICTÉE SUR LE POSTE', '=' * 48, '',
              'Le dossier « %s » contient des programmes tiers non modifiés :' % dossier, '',
              '- %s, version %s (commit %s), licence %s.' % (COMPOSANT, VERSION, COMMIT, LICENCE),
              '  %s' % copyright_,
              '  Projet : %s ; publication : %s.' % (PROJET, PUBLICATION),
              '  Archive d’origine : %s (%d octets, SHA-256 %s).' % (archive['nom'], archive['octets'], archive['sha256']),
              '  La bibliothèque ggml (fichiers ggml*) fait partie de cette publication, sous la même licence MIT.',
              '  Texte de la licence : %s.' % LICENCE_FICHIER, '',
              'Fichiers repris : ' + ', '.join(BINAIRES[plateforme]) + '.', '']
    if plateforme == 'windows':
        lignes += ['Composant Microsoft Visual C++ 2015-2022 (MSVCP140.dll, VCRUNTIME140.dll, VCRUNTIME140_1.dll, VCOMP140.DLL) : nécessaire au',
                   'moteur, il n’est PAS redistribué avec AxiorHub Pilote. S’il manque sur le poste, l’installer depuis le lien officiel de',
                   'Microsoft : ' + VC_REDIST + ' (conditions de licence de Microsoft).', '']
    else:
        lignes += ['Dépendances du système, non copiées : libgomp1 (paquet Ubuntu, bibliothèque d’exécution de GCC) et glibc 2.34 ou plus',
                   'récente.', '']
    lignes += ['Modèles de reconnaissance : ils ne sont pas inclus. L’administrateur les télécharge à la demande depuis',
               '%s (révision %s) : modèles Whisper d’OpenAI convertis au format ggml, licence MIT.' % (MODELES_DEPOT, MODELES_REVISION), '',
               'Tailles et empreintes SHA-256 de chaque fichier : %s.' % MANIFESTE, '']
    return '\n'.join(lignes)


def _ecrire(dossier, nom, donnees, mode):
    p = dossier / nom
    p.write_bytes(donnees)
    try:
        os.chmod(p, mode)
    except OSError:
        pass


def ecrire_copie(sortie, plateforme, binaires, licence, archive):
    """Écrit ``sortie/<plateforme>/`` (binaires, licence, avis, manifeste) et renvoie le manifeste."""
    dossier = Path(sortie) / plateforme
    if dossier.exists():
        shutil.rmtree(dossier)
    dossier.mkdir(parents=True)
    for nom in BINAIRES[plateforme]:
        _ecrire(dossier, nom, binaires[nom], 0o755)
    _ecrire(dossier, LICENCE_FICHIER, licence, 0o644)
    _ecrire(dossier, NOTICE, avis(plateforme, archive, copyright_de(licence)).encode('utf-8'), 0o644)
    fichiers = {}
    for nom in BINAIRES[plateforme] + TEXTES:
        p = dossier / nom
        fichiers[nom] = {'octets': p.stat().st_size, 'sha256': sha256(p)}
    manifeste = {'composant': COMPOSANT, 'version': VERSION, 'commit': COMMIT, 'licence': LICENCE, 'projet': PROJET,
                 'plateforme': plateforme, 'executable': EXECUTABLE[plateforme], 'dependances_systeme': DEPENDANCES[plateforme],
                 'archive': {k: archive[k] for k in ('nom', 'octets', 'sha256')}, 'fichiers': fichiers,
                 'octets': sum(v['octets'] for v in fichiers.values())}
    _ecrire(dossier, MANIFESTE, (json.dumps(manifeste, ensure_ascii=False, indent=1, sort_keys=True) + '\n').encode('utf-8'), 0o644)
    return manifeste


def preparer(archives, sortie, attendus=None):
    """Vérifie les deux archives puis écrit ``sortie/windows`` et ``sortie/linux``. ``attendus`` remplace ``ARCHIVES`` (essais)."""
    archives, attendus = Path(archives), attendus or ARCHIVES
    for plateforme in ('windows', 'linux'):
        verifier_archive(archives / attendus[plateforme]['nom'], attendus[plateforme])
    linux, licence = lire_linux(archives / attendus['linux']['nom'], attendus['linux']['prefixe'])
    windows = lire_windows(archives / attendus['windows']['nom'], attendus['windows']['prefixe'])
    copyright_de(licence)                       # refus avant toute écriture
    Path(sortie).mkdir(parents=True, exist_ok=True)
    return {'windows': ecrire_copie(sortie, 'windows', windows, licence, attendus['windows']),
            'linux': ecrire_copie(sortie, 'linux', linux, licence, attendus['linux'])}


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 2:
        print(__doc__)
        return 2
    sortie = Path(argv[1]).resolve()
    resultats = preparer(Path(argv[0]).resolve(), sortie)
    for plateforme, m in resultats.items():
        print('%s : %d fichiers (manifeste compris), %d octets (%.1f Mo) dans %s' % (
            plateforme, len(m['fichiers']) + 1, m['octets'] + (sortie / plateforme / MANIFESTE).stat().st_size,
            m['octets'] / 1e6, sortie / plateforme))
        for nom, v in sorted(m['fichiers'].items()):
            print('  %10d  %s  %s' % (v['octets'], v['sha256'], nom))
    return 0


if __name__ == '__main__':
    sys.exit(main())
