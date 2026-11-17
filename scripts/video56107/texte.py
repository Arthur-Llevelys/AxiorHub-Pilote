"""5.6.107 (lot V) : texte de la voix off, scénario, minutage et sous-titres de la vidéo de démonstration (fonctions pures, sans dépendance).

- ``lire_voix`` lit ``voix.txt`` : « [numéro] Titre | durée visée s » puis la phrase du plan ; les lignes « # » sont des commentaires.
- ``minutage`` calcule la durée de chaque plan : jamais moins que la durée visée par le scénario, et assez pour la voix
  (``avance_s`` de silence au début du plan, puis la voix, puis ``marge_s``). Sans voix enregistrée, la durée de la voix est estimée
  d'après le nombre de mots (et annoncée comme estimée).
- ``sous_titres`` découpe chaque phrase en répliques d'au plus deux lignes de 42 caractères, réparties sur la durée réelle de la voix
  du plan (au prorata des caractères), puis ``srt`` et ``vtt`` les écrivent ; ``verifier_srt`` et ``verifier_vtt`` contrôlent un fichier.
- ``texte_parle`` applique la table de prononciation de la langue (le sous-titre garde l'écriture d'origine).
- Langues (``langues.json``) : ``fr`` (``voix.txt``), ``en``, ``de``, ``es`` (``voix.<langue>.txt``) ; scénario commun, légendes, carton de
  fin, cibles et voix propres à chaque langue (``plan_en``) ; fichiers de sortie suffixés (``.en``…), le français sans suffixe.
"""
import json
from pathlib import Path
import re

ICI = Path(__file__).resolve().parent
RACINE = ICI.parent.parent
ENTETE = re.compile(r'^\[(\d+)\]\s*(.+?)\s*\|\s*(\d+(?:[.,]\d+)?)\s*s\s*$')
MOTS_MIN, MOTS_MAX = 210, 240     # visé : 220 à 240 ; texte validé par l'avocat le 17/11/2026 : 217 mots
LIAISONS = frozenset({'et', 'ou', 'puis', 'mais', 'où', 'qui', 'sans', 'avant', 'dans', 'pour', 'avec'})   # bonne coupure : juste avant
DOUBLES = ':;!?»'             # ponctuation précédée d'une espace en français : jamais en tête de ligne ni de réplique


class ErreurTexte(ValueError):
    """Texte de la voix, scénario ou sous-titres mal formés."""


def charger_json(nom):
    return json.loads((ICI / nom).read_text(encoding='utf-8'))


def scenario():
    return charger_json('scenario.json')


def reglages():
    return charger_json('reglages.json')


LANGUES = ('fr', 'en', 'de', 'es')


def langue(code='fr'):
    """Réglages d'une langue (``langues.json``) ; ``ErreurTexte`` pour une langue inconnue."""
    toutes = charger_json('langues.json')
    if code not in LANGUES or code not in toutes:
        raise ErreurTexte('langue inconnue : %r (attendu : %s)' % (code, ', '.join(LANGUES)))
    return toutes[code]


def chemin_voix(code='fr'):
    langue(code)
    return ICI / ('voix.txt' if code == 'fr' else 'voix.%s.txt' % code)


def plan_en(plan, code='fr'):
    """Plan du scénario commun adapté à la langue : légende, carton de fin et cibles remplacés par ceux de ``langues.json``."""
    conf = langue(code)
    out = json.loads(json.dumps(plan))
    propre = conf.get('plans', {}).get(plan['nom'], {})
    for cle in ('legende', 'page'):
        if cle in propre:
            out[cle] = propre[cle]
    if propre.get('cibles'):
        out['cibles'] = dict(out.get('cibles') or {}, **propre['cibles'])
    if 'fin' in out and conf.get('fin'):
        out['fin'] = dict(out['fin'], **conf['fin'])
    return out


def lire_voix(chemin=None, code='fr'):
    """Liste ordonnée de plans ``{'id', 'titre', 'duree_visee', 'texte'}`` lus dans ``voix.txt`` (ou ``voix.<langue>.txt``)."""
    brut = Path(chemin or chemin_voix(code)).read_text(encoding='utf-8')
    plans, courant = [], None
    for n, ligne in enumerate(brut.splitlines(), 1):
        s = ligne.strip()
        if not s or s.startswith('#'):
            continue
        m = ENTETE.match(s)
        if m:
            courant = {'id': int(m.group(1)), 'titre': m.group(2), 'duree_visee': float(m.group(3).replace(',', '.')), 'texte': ''}
            plans.append(courant)
        elif courant is None:
            raise ErreurTexte('ligne %d : texte avant le premier plan' % n)
        else:
            courant['texte'] = (courant['texte'] + ' ' + s).strip()
    if not plans:
        raise ErreurTexte('aucun plan dans le texte de la voix')
    return plans


def compter_mots(texte):
    """Mots prononcés : jetons séparés par des blancs contenant au moins une lettre ou un chiffre (« l'IA » compte pour un)."""
    return len([w for w in str(texte).split() if re.search(r'\w', w)])


def phrases(texte):
    """Phrases du texte : une fin de phrase est « . », « ! » ou « ? » suivi d'un blanc ou de la fin (« axiorhub.com » n'en est pas une)."""
    return [p for p in re.split(r'(?<=[.!?])\s+', str(texte).strip()) if p]


def controler(plans_voix, plans_scenario, mots=None):
    """Anomalies (liste vide si tout est cohérent) : mêmes plans dans le même ordre, mêmes durées visées, une phrase par plan,
    longueur totale dans la fourchette visée, aucun plan muet sauf la fin."""
    out = []
    ids_v, ids_s = [p['id'] for p in plans_voix], [p['id'] for p in plans_scenario]
    if ids_v != ids_s:
        out.append('plans de la voix %r différents de ceux du scénario %r' % (ids_v, ids_s))
    if ids_s != list(range(1, len(ids_s) + 1)):
        out.append('plans du scénario non numérotés de 1 à %d' % len(ids_s))
    scen = {p['id']: p for p in plans_scenario}
    for p in plans_voix:
        s = scen.get(p['id'])
        if s and abs(float(s['duree_visee']) - p['duree_visee']) > 1e-6:
            out.append('plan %d : durée visée %s s dans la voix, %s s dans le scénario' % (p['id'], p['duree_visee'], s['duree_visee']))
        if not p['texte']:
            out.append('plan %d : aucun texte' % p['id'])
        elif len(phrases(p['texte'])) != 1:
            out.append('plan %d : %d phrases (une seule attendue)' % (p['id'], len(phrases(p['texte']))))
    total = sum(compter_mots(p['texte']) for p in plans_voix)
    bas, haut = mots or (MOTS_MIN, MOTS_MAX)
    if not bas <= total <= haut:
        out.append('%d mots (de %d à %d attendus)' % (total, bas, haut))
    return out


def texte_parle(texte, prononciation=None):
    """Texte remis à la synthèse : remplacements de la table (mots entiers, clés les plus longues d'abord)."""
    table = prononciation if prononciation is not None else langue('fr').get('prononciation', {})
    out = str(texte)
    for cle in sorted(table, key=len, reverse=True):
        out = re.sub(r'(?<![\w.])' + re.escape(cle) + r'(?![\w])', table[cle], out)
    return out


# ------------------------------------------------------------------------------------------------ minutage
def minutage(plans_voix, durees_voix=None, regl=None):
    """Plans avec ``duree`` (durée de l'image), ``voix_s`` (durée de la voix, mesurée ou estimée), ``estime`` et ``debut`` théorique.

    ``durees_voix`` : {id: secondes} mesurées sur les WAV ; absent ou incomplet = estimation d'après le nombre de mots."""
    regl = regl or reglages()
    v = regl['voix']
    avance, marge, debit = float(v['avance_s']), float(v['marge_s']), float(v['mots_par_seconde_estimes'])
    durees_voix = durees_voix or {}
    out, t = [], 0.0
    for p in plans_voix:
        mesure = durees_voix.get(p['id'], durees_voix.get(str(p['id'])))
        voix = float(mesure) if mesure is not None else (compter_mots(p['texte']) / debit if p['texte'] else 0.0)
        besoin = avance + voix + marge if voix else 0.0
        duree = round(max(float(p['duree_visee']), besoin), 2)
        out.append({'id': p['id'], 'titre': p['titre'], 'duree_visee': p['duree_visee'], 'voix_s': round(voix, 3),
                    'estime': mesure is None, 'duree': duree, 'debut': round(t, 3)})
        t += duree
    return out


# ------------------------------------------------------------------------------------------------ sous-titres
def _lignes(texte, largeur):
    """Une ou deux lignes d'au plus ``largeur`` caractères, coupées au blanc le plus proche du milieu (lignes équilibrées) ; plus de deux
    lignes seulement si le texte ne tient pas en deux (remplissage au fil des mots)."""
    texte = ' '.join(str(texte).split())
    if len(texte) <= largeur:
        return [texte] if texte else []
    blancs = [i for i, c in enumerate(texte) if c == ' ' and i <= largeur and len(texte) - i - 1 <= largeur
              and texte[i + 1] not in DOUBLES]   # jamais « : » ni « ; » en début de ligne
    if blancs:
        i = min(blancs, key=lambda k: abs(len(texte) / 2.0 - k))
        return [texte[:i], texte[i + 1:]]
    lignes, courante = [], ''
    for mot in texte.split():
        if courante and len(courante) + 1 + len(mot) > largeur:
            lignes.append(courante)
            courante = mot
        else:
            courante = (courante + ' ' + mot).strip()
    if courante:
        lignes.append(courante)
    return lignes


def _decouper(mots, n, largeur, nb_lignes):
    """Meilleur découpage en ``n`` répliques (programmation dynamique) : longueurs proches de la moyenne, coupure de préférence après une
    ponctuation (« , », « : », « ; », « . ») ; chaque réplique tient en ``nb_lignes`` lignes. None si impossible."""
    cible = len(' '.join(mots)) / float(n)
    W = len(mots)
    infini = float('inf')

    def cout(i, j):   # réplique mots[i:j]
        texte = ' '.join(mots[i:j])
        if len(_lignes(texte, largeur)) > nb_lignes or (j < W and mots[j][0] in DOUBLES):
            return infini
        penalite = 0.0 if j == W or mots[j - 1][-1] in ',:;.!?' else 2.0 if mots[j].lower() in LIAISONS else 6.0
        return (len(texte) - cible) ** 2 / max(cible, 1.0) + penalite

    meilleur = {(0, 0): (0.0, None)}
    for k in range(1, n + 1):
        for j in range(k, W + 1):
            options = [(meilleur[(k - 1, i)][0] + cout(i, j), i) for i in range(k - 1, j) if (k - 1, i) in meilleur]
            options = [o for o in options if o[0] < infini]
            if options:
                meilleur[(k, j)] = min(options)
    if (n, W) not in meilleur:
        return None
    coupes, j = [], W
    for k in range(n, 0, -1):
        i = meilleur[(k, j)][1]
        coupes.append(' '.join(mots[i:j]))
        j = i
    return coupes[::-1]


def repliques(texte, largeur=42, nb_lignes=2):
    """Répliques équilibrées d'au plus ``nb_lignes`` lignes de ``largeur`` caractères (le moins de répliques possible)."""
    mots = str(texte).split()
    for n in range(1, len(mots) + 1):
        morceaux = _decouper(mots, n, largeur, nb_lignes)
        if morceaux:
            return ['\n'.join(_lignes(m, largeur)) for m in morceaux]
    return []


def sous_titres(plans_voix, plans_reels, regl=None):
    """Répliques ``(debut, fin, texte)`` en secondes depuis le début de la vidéo montée.

    ``plans_reels`` : [{'id', 'debut', 'duree', 'voix_s'}] (minutage mesuré de l'image) ; la voix d'un plan commence ``avance_s`` après
    le début du plan et dure ``voix_s`` ; chaque réplique reçoit une part de cette durée proportionnelle à son nombre de caractères."""
    regl = regl or reglages()
    st, avance = regl['sous_titres'], float(regl['voix']['avance_s'])
    reels = {int(p['id']): p for p in plans_reels}
    out = []
    for p in plans_voix:
        r = reels.get(p['id'])
        if not r or not p['texte']:
            continue
        morceaux = repliques(p['texte'], int(st['caracteres_par_ligne']), int(st['lignes']))
        total = sum(len(m) for m in morceaux) or 1
        debut = float(r['debut']) + avance
        fin_plan = float(r['debut']) + float(r['duree'])
        voix = min(float(r.get('voix_s') or 0) or (fin_plan - debut), fin_plan - debut)
        t = debut
        for i, m in enumerate(morceaux):
            d = max(float(st['duree_min_s']), voix * len(m) / total)
            fin = min(t + d, fin_plan) if i < len(morceaux) - 1 else min(max(t + d, debut + voix), fin_plan)
            out.append((round(t, 3), round(max(fin, t + 0.5), 3), m))
            t = fin
    return out


def _horodatage(s, sep):
    ms = int(round(s * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    sec, ms = divmod(ms, 1000)
    return '%02d:%02d:%02d%s%03d' % (h, m, sec, sep, ms)


def srt(repl):
    return ''.join('%d\n%s --> %s\n%s\n\n' % (i, _horodatage(a, ','), _horodatage(b, ','), t) for i, (a, b, t) in enumerate(repl, 1))


def vtt(repl):
    return 'WEBVTT\n\n' + ''.join('%d\n%s --> %s\n%s\n\n' % (i, _horodatage(a, '.'), _horodatage(b, '.'), t) for i, (a, b, t) in enumerate(repl, 1))


_SRT = re.compile(r'^(\d+)\n(\d\d):(\d\d):(\d\d),(\d{3}) --> (\d\d):(\d\d):(\d\d),(\d{3})\n((?:.+\n?)+)$')
_VTT = re.compile(r'^(?:(\S+)\n)?(\d\d):(\d\d):(\d\d)\.(\d{3}) --> (\d\d):(\d\d):(\d\d)\.(\d{3})(?: [^\n]*)?\n((?:.+\n?)+)$')


def _secondes(g):
    return int(g[0]) * 3600 + int(g[1]) * 60 + int(g[2]) + int(g[3]) / 1000


def _verifier(blocs, motif, numerote):
    if not blocs:
        raise ErreurTexte('aucune réplique')
    precedent = -1.0
    for i, b in enumerate(blocs, 1):
        m = motif.match(b + '\n')
        if not m:
            raise ErreurTexte('réplique %d mal formée : %r' % (i, b[:80]))
        if numerote and int(m.group(1)) != i:
            raise ErreurTexte('réplique %d numérotée %s' % (i, m.group(1)))
        a, z = _secondes(m.groups()[1:5]), _secondes(m.groups()[5:9])
        if not (precedent <= a < z):
            raise ErreurTexte('réplique %d : horaires incohérents (%.3f → %.3f après %.3f)' % (i, a, z, precedent))
        if any(len(l) > 42 for l in m.group(10).strip().split('\n')) or len(m.group(10).strip().split('\n')) > 2:
            raise ErreurTexte('réplique %d : plus de deux lignes ou ligne de plus de 42 caractères' % i)
        precedent = z
    return len(blocs)


def verifier_srt(contenu):
    """Nombre de répliques d'un SRT bien formé (numéros consécutifs, « HH:MM:SS,mmm --> … », horaires croissants) ; sinon ErreurTexte."""
    if contenu.startswith('﻿'):
        contenu = contenu[1:]
    blocs = [b.strip('\n') for b in contenu.replace('\r\n', '\n').strip().split('\n\n') if b.strip()]
    return _verifier(blocs, _SRT, True)


def verifier_vtt(contenu):
    """Nombre de répliques d'un WebVTT bien formé (en-tête « WEBVTT », « HH:MM:SS.mmm --> … », horaires croissants) ; sinon ErreurTexte."""
    contenu = contenu.replace('\r\n', '\n')
    if not (contenu.startswith('WEBVTT\n') or contenu == 'WEBVTT'):
        raise ErreurTexte('en-tête WEBVTT absent')
    blocs = [b.strip('\n') for b in contenu.split('\n\n')[1:] if b.strip()]
    return _verifier(blocs, _VTT, False)
