#!/usr/bin/env python3
"""5.6.107 (lot V) : vidéo de démonstration de 90 secondes d'AxiorHub Pilote, réenregistrable à chaque version (outil de développement :
jamais dans un paquet, jamais sur le serveur du cabinet ; données fictives seulement, aucun service en ligne).

Étapes (sous-commandes) :
  verifier     contrôle le texte de la voix (``scripts/video56107/voix.txt`` ou ``voix.<langue>.txt``) et le scénario ;
  voix         synthèse vocale locale, un WAV par plan (moteur de la langue : Kokoro, Piper ; replis System.Speech, espeak-ng) ;
  image        enregistrement de l'image par Playwright (Chromium 1920 × 1080) à partir du mode Démo (lot M) dans la langue choisie ou,
               pour le français seulement, du banc des parcours ; chaque plan dure ce que demande sa voix (ou sa durée visée) ;
  sous-titres  ``.srt`` et ``.vtt`` de la langue, calés sur le minutage réel de l'image ;
  montage      voix assemblée (Python), puis MP4 H.264/AAC, WebM VP9/Opus et affiche PNG par ffmpeg (variable ``FFMPEG`` ; sans elle,
               arrêt propre avec un message clair, code 3) ;
  tout         toutes les étapes dans l'ordre.

Langues (``--langue fr|en|de|es``, ``scripts/video56107/langues.json``) : scénario commun ; voix, légendes, carton de fin, sous-titres et
données de la démo propres à la langue. Fichiers : le français sans suffixe (``axiorhub-demo-90s.mp4``, ``minutage.json``, ``voix/``),
les autres langues suffixées (``axiorhub-demo-90s.en.mp4``, ``minutage.en.json``, ``voix-en/``, ``affiche.en.png``…).

Usage : ``python scripts/video-demo.py tout [--langue fr] [--sortie DOSSIER] [--source auto|demo|banc] [--moteur kokoro|piper|windows|espeak]
[--plans 1,2] [--voir] [--captures] [--sans-voix]``.
Sortie par défaut : ``../video-5.6.107`` à côté du dépôt (réglage ``sortie`` de ``scripts/video56107/reglages.json``).
Codes de sortie : 0 réussi ; 1 étape faite avec remarques ou contrôle en échec ; 2 Playwright, synthèse vocale ou démo dans la langue
absents ; 3 ffmpeg absent.
"""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'scripts'))

from video56107 import montage as mt   # noqa: E402
from video56107 import texte as tx     # noqa: E402

LISEZMOI = (Path(__file__).resolve().parent / 'video56107' / 'LISEZMOI.txt')


def dossier_sortie(valeur=None):
    regl = tx.reglages()
    chemin = Path(valeur or regl['sortie'])
    return chemin if chemin.is_absolute() else (ROOT / chemin).resolve()


def fichiers(sortie, langue='fr'):
    """Chemins de travail et de sortie d'une langue (français sans suffixe : les noms d'avant les langues ne changent pas)."""
    s = tx.langue(langue)['suffixe']
    nom = tx.reglages()['nom'] + s
    return {'nom': nom, 'voix': sortie / ('voix' if langue == 'fr' else 'voix-' + langue), 'minutage': sortie / ('minutage%s.json' % s),
            'brut': sortie / 'brut' / ('axiorhub-demo-brut%s.webm' % s), 'affiche': 'affiche%s.png' % s, 'affiche_readme': sortie / ('affiche-readme%s.jpg' % s),
            'srt': sortie / (nom + '.srt'), 'vtt': sortie / (nom + '.vtt'), 'mp4': sortie / (nom + '.mp4'), 'webm': sortie / (nom + '.webm')}


def _durees_voix(f):
    d = f['voix'] / 'durees.json'
    if not d.exists():
        return None
    return {int(k): v for k, v in json.loads(d.read_text(encoding='utf-8'))['durees'].items()}


def etape_verifier(args, sortie):
    plans = tx.lire_voix(code=args.langue)
    anomalies = tx.controler(plans, tx.scenario()['plans'], tx.langue(args.langue)['mots'])
    print('Texte de la voix (%s) : %d plans, %d mots.' % (args.langue, len(plans), sum(tx.compter_mots(p['texte']) for p in plans)))
    for a in anomalies:
        print('  ANOMALIE : ' + a)
    return 1 if anomalies else 0


def etape_voix(args, sortie):
    from video56107 import voix
    try:
        r = voix.synthetiser(tx.lire_voix(code=args.langue), fichiers(sortie, args.langue)['voix'], choix=args.moteur or None, code=args.langue)
    except voix.ErreurVoix as ex:
        print('Voix : %s' % ex, file=sys.stderr)
        return 2
    print('Voix (%s) : %s — %s' % (args.langue, r['moteur'], r['voix']))
    if r['moteur'] != tx.langue(args.langue)['voix']['moteur']:
        print('  ATTENTION : moteur de repli, pas la voix retenue pour cette langue (%s).' % tx.langue(args.langue)['voix']['moteur'])
    for ident, d in sorted(r['durees'].items()):
        print('  plan %d : %.2f s' % (ident, d))
    return 0


def etape_image(args, sortie):
    import importlib.util
    if importlib.util.find_spec('playwright') is None:
        print('Playwright pour Python est absent : pip install playwright (outil de développement seulement).', file=sys.stderr)
        return 2
    from video56107 import film
    f = fichiers(sortie, args.langue)
    m = tx.minutage(tx.lire_voix(code=args.langue), _durees_voix(f))
    if any(p['estime'] for p in m):
        print('Image : voix non enregistrée, durées estimées d’après le nombre de mots (lancer « voix » d’abord pour les durées réelles).')
    choisis = {int(x) for x in args.plans.split(',') if x.strip()} if args.plans else None
    try:
        r = film.tourner(sortie, m, mode=args.source, choisis=choisis, voir=args.voir, captures=args.captures, langue=args.langue,
                         brut=f['brut'], minutage=f['minutage'])
    except film.LangueIndisponible as ex:
        print('Image : %s' % ex, file=sys.stderr)
        return 2
    print('Image : %s (source %s, langue %s, navigateur %s), %d plans, %.1f s.' % (
        r['video_brute'] or 'AUCUNE VIDÉO', r['source'], r['langue'], r['navigateur'], len(r['plans']), r['duree_totale_s']))
    for p in r['plans']:
        print('  plan %d — %-46s visée %4.1f s, cible %5.2f s, actions %5.2f s, réelle %5.2f s%s' % (
            p['id'], p['titre'], p['duree_visee'], p['duree_cible'], p['actions_s'], p['duree_reelle'], ' — %d remarque(s)' % len(p['remarques']) if p['remarques'] else ''))
        for rq in p['remarques']:
            print('      remarque : ' + rq)
    for u in r['requetes_bloquees']:
        print('  requête externe bloquée : ' + u)
    for e in r['erreurs_javascript']:
        print('  erreur JavaScript : ' + e)
    return 0 if r['video_brute'] and not any(p['remarques'] for p in r['plans']) else 1


def _minutage_reel(f):
    if not f['minutage'].exists():
        raise SystemExit('%s absent : lancer d’abord « image ».' % f['minutage'])
    r = json.loads(f['minutage'].read_text(encoding='utf-8'))
    debut = float(r['debut_premier_plan_s'])
    plans = [{'id': p['id'], 'debut': round(float(p['debut']) - debut, 3), 'duree': float(p['duree_reelle']), 'voix_s': p.get('voix_s')}
             for p in r['plans']]
    return r, plans


def etape_sous_titres(args, sortie):
    f = fichiers(sortie, args.langue)
    _r, plans = _minutage_reel(f)
    mesurees = _durees_voix(f) or {}          # voix refaite sans retourner l'image : durées de la voix actuelle
    for p in plans:
        p['voix_s'] = mesurees.get(p['id'], p['voix_s'])
    repl = tx.sous_titres(tx.lire_voix(code=args.langue), plans)
    f['srt'].write_text(tx.srt(repl), encoding='utf-8')
    f['vtt'].write_text(tx.vtt(repl), encoding='utf-8')
    n1 = tx.verifier_srt(f['srt'].read_text(encoding='utf-8'))
    n2 = tx.verifier_vtt(f['vtt'].read_text(encoding='utf-8'))
    print('Sous-titres (%s) : %d répliques (.srt), %d (.vtt).' % (args.langue, n1, n2))
    return 0


def etape_montage(args, sortie):
    try:
        ffmpeg = mt.trouver_ffmpeg()
    except mt.FfmpegAbsent as ex:
        print('Montage impossible : %s' % ex, file=sys.stderr)
        return 3
    from video56107 import voix
    regl, f = tx.reglages(), fichiers(sortie, args.langue)
    r, plans = _minutage_reel(f)
    brut = Path(r['video_brute'] or f['brut'])
    if not brut.exists():
        raise SystemExit('vidéo brute absente (%s) : lancer d’abord « image ».' % brut)
    duree = sum(p['duree'] for p in plans)
    wav = None
    if not args.sans_voix:
        durees = f['voix'] / 'durees.json'
        if not durees.exists():
            raise SystemExit('voix absente : lancer « voix » (ou --sans-voix pour une vidéo muette).')
        dj = json.loads(durees.read_text(encoding='utf-8'))
        if dj.get('langue', 'fr') != args.langue:
            raise SystemExit('la voix de %s est en « %s », pas en « %s »' % (f['voix'], dj.get('langue'), args.langue))
        fichiers_voix = {int(k): v for k, v in dj['fichiers'].items()}
        wav = f['voix'] / 'voix-complete.wav'
        voix.assembler(fichiers_voix, {p['id']: p['debut'] for p in plans if p['id'] in fichiers_voix}, duree, wav, float(regl['voix']['avance_s']))
    liste = mt.commandes(ffmpeg, brut, wav, r['debut_premier_plan_s'], duree, sortie, f['nom'], regl,
                         int(regl['image']['largeur']), int(regl['image']['hauteur']), int(regl['image']['images_par_seconde']), affiche=f['affiche'])
    print('Montage (%s) avec %s :' % (args.langue, ffmpeg))
    mt.monter(ffmpeg, liste)
    taille = mt.affiche_allegee(ffmpeg, sortie / f['affiche'], f['affiche_readme'])
    print('  affiche allégée pour les README : %s (%d Ko)' % (f['affiche_readme'].name, taille // 1024))
    print('Montage : %s.mp4, %s.webm, %s dans %s' % (f['nom'], f['nom'], f['affiche'], sortie))
    return 0


def ecrire_lisezmoi(sortie):
    minutage = sortie / 'minutage.json'
    duree = '90'
    if minutage.exists():
        duree = '%.0f' % json.loads(minutage.read_text(encoding='utf-8'))['duree_totale_s']
    nom = tx.reglages()['nom']
    (sortie / 'LISEZMOI.txt').write_text(LISEZMOI.read_text(encoding='utf-8').replace('{NOM}', nom).replace('{DUREE}', duree), encoding='utf-8')


def main(argv=None):
    parser = argparse.ArgumentParser(description='Vidéo de démonstration d’AxiorHub Pilote (outil de développement).')
    parser.add_argument('etape', choices=('verifier', 'voix', 'image', 'sous-titres', 'montage', 'tout'))
    parser.add_argument('--langue', choices=tx.LANGUES, default='fr')
    parser.add_argument('--sortie', default='')
    parser.add_argument('--source', choices=('auto', 'demo', 'banc'), default='auto')
    parser.add_argument('--plans', default='', help='numéros de plans séparés par des virgules (essai) ; vide : tous')
    parser.add_argument('--voir', action='store_true', help='navigateur visible')
    parser.add_argument('--captures', action='store_true', help='captures PNG aux moments clés de chaque plan (vérification)')
    parser.add_argument('--sans-voix', action='store_true', help='montage sans la voix (vidéo muette)')
    parser.add_argument('--moteur', choices=('kokoro', 'piper', 'windows', 'espeak'), default='',
                        help='synthèse vocale ; défaut : moteur de la langue (langues.json) s’il est prêt, sinon ses replis')
    args = parser.parse_args(argv)
    sortie = dossier_sortie(args.sortie)
    sortie.mkdir(parents=True, exist_ok=True)
    etapes = {'verifier': etape_verifier, 'voix': etape_voix, 'image': etape_image, 'sous-titres': etape_sous_titres, 'montage': etape_montage}
    ordre = ['verifier', 'voix', 'image', 'sous-titres', 'montage'] if args.etape == 'tout' else [args.etape]
    code = 0
    for nom in ordre:
        if nom == 'voix' and args.sans_voix:
            continue
        c = etapes[nom](args, sortie)
        if c in (2, 3):
            ecrire_lisezmoi(sortie)
            return c
        code = max(code, c)
    ecrire_lisezmoi(sortie)
    return code


if __name__ == '__main__':
    sys.exit(main())
