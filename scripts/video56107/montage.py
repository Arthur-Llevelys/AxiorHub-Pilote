"""5.6.107 (lot V) : montage de la vidéo de démonstration avec ffmpeg (fourni par la variable d'environnement ``FFMPEG``).

ffmpeg n'est ni fourni ni téléchargé par AxiorHub : son téléchargement est soumis à l'accord du cabinet. Sans ``FFMPEG`` (ou si le chemin
ne désigne pas un ffmpeg utilisable avec les encodeurs nécessaires), ``trouver_ffmpeg`` lève ``FfmpegAbsent`` avec un message clair et
rien n'est écrit.

Étapes (``commandes``) :
1. MP4 : image brute (webm de Playwright, coupée au début du premier plan) + voix assemblée (WAV), H.264 ``yuv420p`` + AAC, ``+faststart``
   (lecture avant la fin du téléchargement), volume de la voix normalisé (``loudnorm``) ;
2. WebM : VP9 + Opus, mêmes entrées ;
3. affiche : première image de la vidéo montée, en PNG.
Les sous-titres ``.srt`` et ``.vtt`` sont écrits par ``texte`` (Python) ; la concaténation des voix par ``voix.assembler`` (Python).
"""
import os
from pathlib import Path
import subprocess

ENCODEURS = ('libx264', 'aac', 'libvpx-vp9', 'libopus')
MESSAGE_ABSENT = ('ffmpeg est introuvable : le montage (MP4, WebM, affiche) en a besoin. Il n’est pas fourni avec AxiorHub et ne doit pas '
                  'être téléchargé sans l’accord du cabinet. Une fois ffmpeg installé, indiquez son chemin dans la variable FFMPEG, par '
                  'exemple : set FFMPEG=C:\\outils\\ffmpeg\\bin\\ffmpeg.exe (Windows) ou export FFMPEG=/usr/bin/ffmpeg (Linux), puis relancez '
                  '« python scripts/video-demo.py montage ». L’image, la voix et les sous-titres déjà produits sont conservés.')


class FfmpegAbsent(RuntimeError):
    """ffmpeg absent ou inutilisable : arrêt propre, sans rien écrire."""


def trouver_ffmpeg(environ=None, verifier=True, executer=subprocess.run):
    """Chemin de ffmpeg pris dans ``FFMPEG`` (jamais cherché ailleurs, jamais téléchargé) ; vérifie qu'il répond et a les encodeurs."""
    environ = os.environ if environ is None else environ
    chemin = str(environ.get('FFMPEG') or '').strip().strip('"')
    if not chemin:
        raise FfmpegAbsent(MESSAGE_ABSENT)
    if not Path(chemin).is_file():
        raise FfmpegAbsent('FFMPEG désigne « %s », qui n’existe pas. %s' % (chemin, MESSAGE_ABSENT))
    if verifier:
        try:
            r = executer([chemin, '-hide_banner', '-encoders'], capture_output=True, text=True, timeout=60)
        except (OSError, subprocess.SubprocessError) as ex:
            raise FfmpegAbsent('FFMPEG (« %s ») ne démarre pas : %s' % (chemin, ex)) from None
        if r.returncode != 0:
            raise FfmpegAbsent('FFMPEG (« %s ») répond en erreur (code %s).' % (chemin, r.returncode))
        manquants = [e for e in ENCODEURS if (' %s ' % e) not in (r.stdout or '')]
        if manquants:
            raise FfmpegAbsent('ffmpeg (« %s ») n’a pas les encodeurs %s : utilisez une version complète (« full » ou « essentials »).'
                               % (chemin, ', '.join(manquants)))
    return chemin


def _s(valeur):
    return '%.3f' % float(valeur)


def commandes(ffmpeg, brut, voix, debut, duree, sortie, nom, regl, largeur=1920, hauteur=1080, ips=30, affiche='affiche.png'):
    """Liste ``[(libellé, argv)]`` du montage. ``brut`` : webm de Playwright ; ``voix`` : WAV assemblé ou None (vidéo muette) ;
    ``debut`` : début du premier plan dans le brut ; ``duree`` : durée totale des plans."""
    sortie = Path(sortie)
    mp4, webm, affiche = sortie / (nom + '.mp4'), sortie / (nom + '.webm'), sortie / affiche
    m = regl['montage']
    image = 'fps=%d,scale=%d:%d:flags=lanczos,format=yuv420p' % (ips, largeur, hauteur)
    entrees = ['-ss', _s(debut), '-i', str(brut)] + (['-i', str(voix)] if voix else [])
    cartes = ['-map', '0:v:0'] + (['-map', '1:a:0', '-af', 'loudnorm=I=-16:LRA=11:TP=-1.5,apad', '-ar', '48000'] if voix else ['-an'])
    commun = [str(ffmpeg), '-y', '-hide_banner', '-loglevel', 'error'] + entrees + cartes + ['-t', _s(duree), '-vf', image]
    out = [('MP4 H.264 + AAC', commun + ['-c:v', 'libx264', '-preset', str(m['mp4']['preset']), '-crf', str(m['mp4']['crf']),
                                         '-profile:v', 'high', '-pix_fmt', 'yuv420p']
            + (['-c:a', 'aac', '-b:a', '%dk' % int(m['mp4']['audio_ko'])] if voix else []) + ['-movflags', '+faststart', str(mp4)]),
           ('WebM VP9 + Opus', commun + ['-c:v', 'libvpx-vp9', '-b:v', '0', '-crf', str(m['webm']['crf']), '-row-mt', '1',
                                         '-deadline', 'good', '-cpu-used', '2', '-pix_fmt', 'yuv420p']
            + (['-c:a', 'libopus', '-b:a', '%dk' % int(m['webm']['audio_ko'])] if voix else []) + [str(webm)]),
           ('affiche PNG (première image)', [str(ffmpeg), '-y', '-hide_banner', '-loglevel', 'error', '-ss', _s(m.get('affiche_decalage_s', 0)),
                                             '-i', str(mp4), '-frames:v', '1', '-update', '1', str(affiche)])]
    return out


def monter(ffmpeg, liste, journal=print, executer=subprocess.run):
    """Exécute les commandes dans l'ordre ; arrêt à la première erreur (``RuntimeError`` avec la fin du message de ffmpeg)."""
    for libelle, argv in liste:
        journal('  %s…' % libelle)
        r = executer(argv, capture_output=True, text=True, timeout=3600)
        if r.returncode != 0:
            raise RuntimeError('ffmpeg : échec de l’étape « %s » : %s' % (libelle, (r.stderr or '').strip()[-800:]))
        cible = Path(argv[-1])
        if not cible.is_file() or cible.stat().st_size == 0:
            raise RuntimeError('ffmpeg : « %s » n’a rien produit (%s)' % (libelle, cible))


AFFICHE_README_LARGEUR = 1280
AFFICHE_README_MAX = 250 * 1024


def commande_affiche_allegee(ffmpeg, png, jpg, qualite):
    """JPEG de l'affiche à 1280 px de large (hauteur proportionnelle, paire) ; ``qualite`` : -q:v de ffmpeg (2 = meilleure, 31 = pire)."""
    return [str(ffmpeg), '-y', '-hide_banner', '-loglevel', 'error', '-i', str(png), '-vf', 'scale=%d:-2:flags=lanczos' % AFFICHE_README_LARGEUR,
            '-q:v', str(int(qualite)), '-frames:v', '1', '-update', '1', str(jpg)]


def affiche_allegee(ffmpeg, png, jpg, executer=subprocess.run):
    """Affiche allégée pour les README (``affiche-readme[.langue].jpg``) : meilleure qualité qui tient sous 250 Ko ; rend la taille."""
    for qualite in (3, 4, 5, 6, 8, 10, 12, 15):
        r = executer(commande_affiche_allegee(ffmpeg, png, jpg, qualite), capture_output=True, text=True, timeout=120)
        if r.returncode != 0:
            raise RuntimeError('ffmpeg : affiche allégée en échec : %s' % (r.stderr or '').strip()[-400:])
        taille = Path(jpg).stat().st_size if Path(jpg).is_file() else 0
        if 0 < taille < AFFICHE_README_MAX:
            return taille
    raise RuntimeError('affiche allégée : toujours plus de 250 Ko (%s)' % jpg)
