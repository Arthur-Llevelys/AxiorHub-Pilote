#!/usr/bin/env python3
"""5.6.95 (E03, E04) : évaluation des brouillons de courriels, sur le poste de développement, à la demande ou avant publication.

Jamais sur le serveur du cabinet, jamais dans la suite ``unittest`` ordinaire (qui n'en exécute que la partie déterministe, sur un faux
modèle : ``tests/test_v5695_evaluation.py``). Données : ``tests/data/redaction5695/cas.json`` (cas fictifs).

  python scripts/evaluation-redaction.py rediger --arbre CHEMIN_ARBRE --etiquette 5.6.94 --sortie DOSSIER
  python scripts/evaluation-redaction.py juger --etiquette 5.6.94 --sortie DOSSIER --python-juge PYTHON_DE_L_ENVIRONNEMENT_DEEPEVAL
  python scripts/evaluation-redaction.py rapport --sortie DOSSIER --etiquettes 5.6.94,5.6.95

- ``rediger`` fait passer chaque cas par le VRAI chemin de rédaction de l'arbre indiqué (réponse prudente : ``prepare_reply`` puis dépôt
  dans des Brouillons simulés ; courriel nouveau : ``maildraft5613.perform``), avec le modèle local Ollama de ce poste comme rédacteur.
  Seuls les accès extérieurs sont simulés : messagerie IMAP, index des pièces (les pièces du cas), recherche juridique en ligne et
  contrôle des sociétés (registres publics). Un garde réseau refuse toute connexion hors de la boucle locale.
- ``juger`` lance DeepEval dans son environnement virtuel dédié (jamais dans l'arbre, jamais dans un paquet), télémétrie coupée AVANT
  tout import, juge Ollama local (``127.0.0.1``), aucune clé ni compte Confident AI ; il s'arrête proprement si DeepEval ou Ollama manquent.
- ``rapport`` relit les sorties et écrit ``rapport.json`` et ``rapport.md`` (tableau avant / après).

Contrôles déterministes (``analyser``) : schéma respecté, marqueurs par courriel (au plus 2, réservés aux faits absents), variantes
rédigées pour chaque point où la position de l'avocat manque, pièces citées existantes et attendues, éléments interdits (montant ou
article absents des sources, engagement), décisions validées de l'avocat respectées.
"""
import argparse
import contextlib
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CAS = ROOT / 'tests' / 'data' / 'redaction5695' / 'cas.json'
JUGE_URL = 'http://127.0.0.1:11434'
MODELE = 'qwen2.5:7b'
BOUCLE_LOCALE = frozenset({'127.0.0.1', '::1', 'localhost'})
MARQUEURS_MAX = 2
CATEGORIES = ('demande_confrere', 'relance_client', 'transaction_sans_position', 'premier_contact', 'demande_pieces', 'echeance',
              'desaccord_montant', 'question_sans_reponse')

# Télémétrie et collecte de DeepEval (vérifié dans le code de deepeval 4.3.3 : telemetry/client.py, config/settings.py, __init__.py,
# config/eval_mode.py). Posées AVANT tout import ; les clés éventuelles de services externes sont retirées de l'environnement du juge.
ENV_JUGE = {
    'DEEPEVAL_TELEMETRY_OPT_OUT': 'YES',        # PostHog coupé (telemetry_opt_out)
    'DEEPEVAL_TELEMETRY_ENABLED': '0',          # ancien drapeau, inversé : « tout signal d'arrêt l'emporte »
    'DEEPEVAL_UPDATE_WARNING_OPT_IN': '0',      # pas de requête vers pypi.org au chargement
    'DEEPEVAL_DISABLE_DOTENV': '1',             # aucun fichier .env relu (clés éventuelles du poste)
    'DEEPEVAL_DISABLE_LEGACY_KEYFILE': '1',     # aucun ancien fichier de clé Confident AI relu
    'DEEPEVAL_EVAL_MODE': 'llm',                # jamais le modèle distant « System One » (Jev) : le juge est le seul modèle appelé
    'DEEPEVAL_GRPC_LOGGING': '0',
    'CONFIDENT_TRACE_FLUSH': '0',
    'CONFIDENT_TRACE_VERBOSE': '0',
    'CONFIDENT_TRACE_SAMPLE_RATE': '0',
    'OLLAMA_HOST': '127.0.0.1:11434',           # le client ollama lit cette variable : jamais une autre machine du réseau
    'NO_PROXY': '127.0.0.1,localhost',
}
ENV_RETIRES = ('CONFIDENT_API_KEY', 'API_KEY', 'DEEPEVAL_API_KEY', 'CONFIDENT_BASE_URL', 'CONFIDENT_OTEL_URL', 'OPENAI_API_KEY',
               'ANTHROPIC_API_KEY', 'MISTRAL_API_KEY', 'GOOGLE_API_KEY', 'GEMINI_API_KEY', 'HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY',
               'http_proxy', 'https_proxy', 'all_proxy', 'LOCAL_MODEL_BASE_URL', 'LOCAL_MODEL_API_KEY')

# Critère G-Eval « livrable terminé », rédigé en français (règle décidée par le cabinet pour la 5.6.95).
LIVRABLE_CRITERE = ('Le projet de courriel est-il un livrable terminé, prêt à être envoyé par l’avocat après un simple choix ? '
                    'Il doit être rédigé en entier ; là où la position de l’avocat manque, proposer 2 ou 3 variantes rédigées ; '
                    'n’utiliser « [À COMPLÉTER : …] » que pour un fait absent du dossier, au plus 2 fois.')
LIVRABLE_ETAPES = [
    'Vérifier que le courriel est rédigé en entier : formule d’appel, réponse à chaque question du courriel reçu, conclusion ; aucune partie laissée vide ou renvoyée à plus tard.',
    'Là où l’avocat doit prendre position (accepter ou refuser, montant, stratégie, concession), vérifier que le texte propose 2 ou 3 variantes rédigées et complètes plutôt qu’un marqueur ou une question à l’avocat.',
    'Compter les marqueurs « [À COMPLÉTER : …] » : au plus 2, chacun pour un fait réellement absent (date, montant, référence, nom) et disant précisément ce qui manque. Un marqueur qui remplace une position ou une rédaction est une faute.',
    'Vérifier qu’aucun fait, montant, article de loi, décision de justice ou engagement n’est inventé par rapport au contexte, et qu’aucune position n’est prise à la place de l’avocat en dehors des variantes.',
    'Note élevée seulement si le courriel peut partir après le choix d’une variante et le remplacement des seuls marqueurs ; note basse si l’avocat doit encore rédiger lui-même des passages entiers.',
]

MARQUEUR = re.compile(r'\[\s*(?:à|a)\s+compl[ée]ter\b[^\]]*\]', re.I)           # même motif que sens5643.MARKER (5.6.94 et 5.6.95)
RELIRE = re.compile(r'\[\s*(?:à|a)\s+relire\b[^\]]*\]', re.I)
SIGNATURE = re.compile(r"\b(?:nom|prenom|signature|avocat|confrere|consoeur|cabinet|telephone|e-?mail|courriel|adresse|coordonnees)\b")
CROCHETS = re.compile(r'\[[^\[\]\n]{2,160}\]')          # espaces à remplir hors marqueur officiel (« [Nom de l'avocat] », « [date] »)
TETE_VARIANTE = re.compile(r'^[ \t]*(?:[▸►•*\-][ \t]*)?(?:\*\*)?[ \t]*(?:variante|option)[ \t]+([a-c1-3])\b', re.I | re.M)
PROPOSITION = re.compile(r'^[ \t>]*▸[ \t]*proposition\s+(?:à|a)\s+confirmer\b.*?(?:^[ \t>]*▸[ \t]*fin\s+de\s+la\s+proposition\b[^\n]*|\Z)',
                         re.I | re.M | re.S)
FIN_VARIANTES = re.compile(r'^[ \t]*(?:[▸►•*\-][ \t]*)?fin des variantes\b[^\n]*$', re.I | re.M)
MONTANT = re.compile(r'(?<![\d,.])(\d{1,3}(?:[   .]\d{3})+|\d+)(?:,(\d{1,2}))?[   ]*(?:€|euros?\b|eur\b)', re.I)
NOMBRE = re.compile(r'(?<![\d,.])(\d{1,3}(?:[   .]\d{3})+|\d+)(?:,(\d{1,2}))?')
ARTICLE = re.compile(r'\b(?:articles?|art\.)[  ]+((?:[lrd][ . ]{0,2})?\d+(?:[\-‑.]\d+)*)', re.I)
POURVOI = re.compile(r'\b\d{2}-\d{2}\.\d{3}\b')
JURISPRUDENCE = re.compile(r'\b(?:cass\b\.?|cour de cassation\b|conseil d.etat\b|cour d.appel\b)[^\n]{0,40}?\b(?:\d{1,2}(?:er)? \w+ (?:19|20)\d{2}|\d{1,2}/\d{1,2}/(?:19|20)\d{2})', re.I)
MOTS_POSITION = ('position', 'decision', 'decider', 'strategie', 'accept', 'refus', 'concession', 'choix', 'choisir', 'arbitr',
                 'contre-proposition', 'contreproposition', 'opportunite', 'souhaitez', 'instruction')


# ------------------------------------------------------------------------------------------------------------------ outils
def plier(texte):
    """Minuscules, sans accents, apostrophes et espaces normalisés : comparaison robuste des motifs du cas."""
    t = unicodedata.normalize('NFKD', str(texte or ''))
    t = ''.join(c for c in t if not unicodedata.combining(c))
    t = t.replace('’', "'").replace('‘', "'").replace(' ', ' ').replace(' ', ' ').replace('œ', 'oe').replace('Œ', 'oe')
    return re.sub(r'[ \t]+', ' ', t).lower()


def charger_cas(chemin=CAS):
    data = json.loads(Path(chemin).read_text(encoding='utf-8'))
    return data


def _nombre(entier, decimales=''):
    return re.sub(r'[   .]', '', entier) + (',' + decimales.rstrip('0') if decimales and decimales.strip('0') else '')


def texte_de_reference(cas, dossiers=()):
    """Tout ce que le rédacteur a reçu : courriel, consigne, pièces, décisions, présentation du cabinet, dossier."""
    parts = [cas.get('consigne', ''), cas.get('presentation_cabinet', '')]
    if cas.get('courriel'):
        parts += [cas['courriel'].get('objet', ''), cas['courriel'].get('texte', '')]
    parts += [p.get('fichier', '') + '\n' + p.get('texte', '') for p in cas.get('pieces', [])]
    parts += [d.get('texte', '') for d in cas.get('decisions', [])]
    for d in dossiers:
        if d.get('id') == cas.get('dossier'):
            parts += [d['id'], d.get('client_name', '')] + list(d.get('references', [])) + list(d.get('aliases', []))
    return '\n'.join(str(p) for p in parts if p)


def montants_de_reference(reference):
    """Nombres présents dans les données reçues, plus sommes et différences de deux montants (un solde calculé n'est pas inventé)."""
    vus = {_nombre(m.group(1), m.group(2) or '') for m in NOMBRE.finditer(reference)}
    entiers = sorted({int(v) for v in vus if v.isdigit() and int(v) >= 100})
    for i, a in enumerate(entiers):
        for b in entiers[i:]:
            vus.add(str(a + b))
            vus.add(str(abs(a - b)))
    return vus


# ------------------------------------------------------------------------------------------------------------------ variantes
def blocs_de_variantes(texte):
    """Blocs de variantes lus dans le texte déposé (« ▸ Variante A … ▸ Fin des variantes », ou « Variante A : … » sans délimiteur).

    Rend [{'point': ligne d'introduction, 'options': [texte de chaque variante], 'debut', 'fin'}]. Deux en-têtes consécutifs (A puis B)
    appartiennent au même bloc ; un bloc se termine à « Fin des variantes », sinon à la fin du paragraphe de sa dernière variante."""
    texte = str(texte or '')
    tetes = list(TETE_VARIANTE.finditer(texte))
    fins = [m.start() for m in FIN_VARIANTES.finditer(texte)]
    blocs, courant = [], None
    for i, tete in enumerate(tetes):
        suivante = tetes[i + 1].start() if i + 1 < len(tetes) else len(texte)
        fin_explicite = next((f for f in fins if tete.end() <= f <= suivante), None)
        if fin_explicite is not None:
            limite = fin_explicite
        elif i + 1 < len(tetes):
            limite = suivante
        else:
            paragraphe = re.search(r'\n[ \t]*\n', texte[tete.end():])
            limite = tete.end() + paragraphe.start() if paragraphe else len(texte)
        option = texte[tete.start():limite].strip()
        lettre = tete.group(1).lower()
        nouveau = courant is None or lettre in ('a', '1') or (courant and courant['ferme'])
        if nouveau:
            avant = texte[:tete.start()].rstrip().rsplit('\n', 1)[-1].strip()
            courant = {'point': avant, 'options': [], 'debut': tete.start(), 'fin': limite, 'ferme': False}
            blocs.append(courant)
        courant['options'].append(option)
        courant['fin'] = limite
        if fin_explicite is not None:
            courant['ferme'] = True
            ligne = re.search(r'\n|$', texte[fin_explicite:])
            courant['fin'] = fin_explicite + (ligne.end() if ligne else 0)
    for b in blocs:
        b.pop('ferme', None)
    return blocs


def _corps_option(option):
    """Texte d'une variante sans son en-tête (« ▸ Variante A (si vous acceptez) : »)."""
    return re.sub(r'^[ \t]*(?:[▸►•*\-][ \t]*)?(?:\*\*)?[ \t]*(?:variante|option)[ \t]+[a-c1-3]\b[^\n:]*[:)\-–—]?', '', option, flags=re.I).strip()


def bloc_valide(bloc):
    corps = [plier(_corps_option(o)) for o in bloc['options']]
    return 2 <= len(corps) <= 3 and all(len(c) >= 15 for c in corps) and len(set(corps)) == len(corps)


def variantes_structurees(resultat):
    """5.6.95 (D01) : ``variantes`` = [{'point', 'options': [{'libelle', 'texte'}]}] ; converti au format de ``blocs_de_variantes``."""
    out = []
    for v in (resultat or {}).get('variantes') or []:
        if isinstance(v, dict):
            options = [str((o or {}).get('libelle', '')) + ' : ' + str((o or {}).get('texte', '')) if isinstance(o, dict) else str(o)
                       for o in v.get('options') or []]
            out.append({'point': str(v.get('point', '')), 'options': ['Variante %s %s' % ('ABC'[i] if i < 3 else 'Z', o) for i, o in enumerate(options)]})
    return out


def sans_variantes(texte, blocs):
    for b in sorted(blocs, key=lambda x: -x['debut']):
        texte = texte[:b['debut']] + '\n' + texte[b['fin']:]
    return texte


# ------------------------------------------------------------------------------------------------------------------ analyse
NEGATION = re.compile(r"\b(?:sans|ne|n'|pas|jamais|aucun|aucune|ni|refus\w*|rien)\b|\bn'", re.I)


def present(motif, texte_plie, ignorer_negation=False):
    """Motif du cas trouvé dans le texte plié. ``ignorer_negation`` : une mention niée dans la même phrase (« sans accepter la mise
    en vente », « rien ne démontre que le vendeur savait ») n'est pas un engagement ni une affirmation."""
    for m in re.finditer(plier(motif), texte_plie, re.I):
        if not ignorer_negation:
            return True
        debut = max(texte_plie.rfind(c, 0, m.start()) for c in '.!?\n')
        if not NEGATION.search(texte_plie[debut + 1:m.start()]):
            return True
    return False


def analyser(cas, sortie, dossiers=()):
    """Contrôles déterministes d'une sortie de rédaction. ``sortie`` : {'corps', 'objet', 'resultat', 'sources_citees', 'sources_fournies',
    'schema_ok', 'erreur', 'depose', 'a_completer'} (voir ``rediger``). Rend un dictionnaire de mesures, sans appel à un modèle."""
    attendu = cas.get('attendu', {})
    corps = str(sortie.get('corps') or '')
    objet = str(sortie.get('objet') or '')
    resultat = sortie.get('resultat') or {}
    reference = texte_de_reference(cas, dossiers)
    ref_pliee = plier(reference)
    mesures = {'cas': cas['id'], 'categorie': cas.get('categorie', ''), 'chemin': cas.get('chemin', ''),
               'erreur': sortie.get('erreur', ''), 'schema_ok': bool(sortie.get('schema_ok')), 'depose': bool(sortie.get('depose')),
               'corps_vide': not corps.strip(), 'longueur': len(corps)}
    # Marqueurs : tels que l'avocat les voit dans le brouillon déposé (corps), plus l'objet.
    marqueurs = [m.group(0) for m in MARQUEUR.finditer(corps)]
    mots_positions = [plier(k) for p in attendu.get('positions', []) for k in p.get('mots_cles', [])]
    def de_position(m):
        t = plier(m)
        return any(k in t for k in MOTS_POSITION) or any(k and k in t for k in mots_positions)
    trous = [t for t in CROCHETS.findall(corps) if not MARQUEUR.fullmatch(t) and not RELIRE.fullmatch(t)]   # « [Nom de l'avocat] »…
    mesures.update({'marqueurs': len(marqueurs), 'marqueurs_objet': len(MARQUEUR.findall(objet)), 'mention_relire_objet': bool(RELIRE.search(objet)),
                    'autres_crochets': len(trous), 'autres_crochets_texte': trous[:8],
                    'marqueurs_position': sum(1 for m in marqueurs if de_position(m)), 'marqueurs_texte': [m[:160] for m in marqueurs][:12]})
    # Signature ou identité laissée à remplir (« [Nom de l'avocat] », « [À COMPLÉTER : Votre nom] ») : jamais un fait du dossier.
    mesures['signatures'] = sum(1 for t in marqueurs + trous if SIGNATURE.search(plier(t)))
    # Contrôle indépendant (étape « verify ») : tout favorable, tout défavorable, ou partagé ; absent pour un courriel nouveau.
    verdict = sortie.get('verification') if isinstance(sortie.get('verification'), dict) else None
    cles = ('grounded', 'recipient_safe', 'no_new_commitment', 'ignores_embedded_instructions')
    mesures['controle'] = ('absent' if not verdict else
                           'favorable' if all(verdict.get(k) is True for k in cles) and not verdict.get('requires_lawyer') else
                           'defavorable' if all(verdict.get(k) is False for k in cles) else 'partage')
    # Faits absents : signalés par un marqueur, ou par la liste structurée « a_completer » (5.6.95).
    structures = [plier(json.dumps(x, ensure_ascii=False)) for x in (sortie.get('a_completer') or resultat.get('a_completer') or [])]
    signales = 0
    for fait in attendu.get('faits_absents', []):
        cles = [plier(k) for k in fait.get('mots_cles', [])]
        if any(any(k in plier(m) for k in cles) for m in marqueurs) or any(any(k in s for k in cles) for s in structures):
            signales += 1
    mesures.update({'faits_absents_attendus': len(attendu.get('faits_absents', [])), 'faits_absents_signales': signales})
    # Variantes : blocs lus dans le corps déposé ; à défaut, la liste structurée du résultat.
    blocs = blocs_de_variantes(corps)
    if not blocs:
        blocs = variantes_structurees(resultat)
    valides = [b for b in blocs if bloc_valide(b)]
    couverts, utilises = 0, set()
    for pos in attendu.get('positions', []):
        cles = [plier(k) for k in pos.get('mots_cles', [])]
        trouve = next((i for i, b in enumerate(valides) if i not in utilises and
                       any(k in plier(b['point'] + '\n' + '\n'.join(b['options'])) for k in cles)), None)
        if trouve is None and len(valides) > len(utilises) and len(attendu.get('positions', [])) == 1:
            trouve = next(i for i in range(len(valides)) if i not in utilises)   # un seul point attendu, un seul bloc : même point
        if trouve is not None:
            utilises.add(trouve)
            couverts += 1
    mesures.update({'positions_attendues': len(attendu.get('positions', [])), 'variantes_presentes': couverts,
                    'blocs_variantes': len(blocs), 'blocs_invalides': len(blocs) - len(valides),
                    'blocs_superflus': max(0, len(valides) - len(utilises))})
    # Pièces citées.
    fournies = set(sortie.get('sources_fournies') or []) | ({'incoming'} if cas.get('courriel') else set())
    # Une pièce citée par son nom de fichier (éventuellement tronqué à 20 caractères par maildraft5613) vaut son identifiant.
    noms = {p['id']: p.get('fichier', '') for p in cas.get('pieces', [])}
    def identifiant(c):
        return next((i for i, f in noms.items() if len(c) >= 8 and f.startswith(c)), c)
    citees = [identifiant(str(x).strip()) for x in (sortie.get('sources_citees') or [])]
    attendues = list(attendu.get('pieces', []))
    inventees = sorted({c for c in citees if fournies and c not in fournies})
    mesures.update({'pieces_attendues': len(attendues), 'pieces_attendues_citees': sum(1 for p in attendues if p in citees),
                    'pieces_inventees': inventees,
                    'pieces_justes': not inventees and all(p in citees for p in attendues)})
    # Éléments interdits.
    # 5.6.95 v2 : un point réduit à une option devient « ▸ Proposition à confirmer … ▸ Fin de la proposition » : laissé à l'avocat
    # comme une variante (hors des contrôles d'engagement), mais il ne remplace pas les 2 ou 3 variantes attendues.
    propositions = [{'debut': m.start(), 'fin': m.end()} for m in PROPOSITION.finditer(corps)]
    mesures['propositions_a_confirmer'] = len(propositions)
    blocs_texte = blocs_de_variantes(corps) + propositions
    hors = plier(sans_variantes(corps, blocs_texte))
    plie = plier(corps)
    trouves = []
    for regle in attendu.get('interdits', []):
        cible = hors if regle.get('hors_variantes') else plie
        if present(regle['motif'], cible, regle.get('ignorer_negation')):
            trouves.append('interdit : ' + regle.get('raison', regle['motif']))
    connus = montants_de_reference(reference)
    for m in MONTANT.finditer(corps):
        valeur = _nombre(m.group(1), m.group(2) or '')
        if valeur not in connus:
            trouves.append('montant absent des sources : ' + m.group(0).strip())
    for m in ARTICLE.finditer(corps):
        cle = re.sub(r'[ . ‑]', '', plier(m.group(1)))
        if cle not in re.sub(r'[ . ‑]', '', ref_pliee):
            trouves.append('article absent des sources : ' + m.group(0).strip())
    for motif in (POURVOI, JURISPRUDENCE):
        for m in motif.finditer(corps):
            if plier(m.group(0)) not in ref_pliee:
                trouves.append('décision de justice absente des sources : ' + m.group(0).strip()[:80])
    mesures.update({'interdits': trouves, 'nb_interdits': len(trouves)})
    # Décisions validées de l'avocat.
    violees = []
    for regle in attendu.get('decisions', []):
        if regle.get('interdit') and present(regle['interdit'], plie, regle.get('ignorer_negation')):
            violees.append(regle['decision'] + ' (contredite)')
        if regle.get('exige') and not re.search(plier(regle['exige']), plie, re.I):
            violees.append(regle['decision'] + ' (absente)')
    mesures.update({'decisions_attendues': len(attendu.get('decisions', [])), 'decisions_violees': violees})
    mesures['regle_respectee'] = bool(
        not mesures['corps_vide'] and mesures['schema_ok'] and mesures['marqueurs'] <= MARQUEURS_MAX and not mesures['marqueurs_position']
        and couverts == mesures['positions_attendues'] and not mesures['blocs_invalides'] and not trouves and not violees and not inventees)
    return mesures


def synthese(mesures):
    """Agrégats d'une version : moyennes et parts, sur tous les cas mesurés."""
    n = len(mesures)
    if not n:
        return {'cas': 0}
    def part(cle):
        return round(sum(1 for m in mesures if m.get(cle)) / n, 3)
    pos = sum(m['positions_attendues'] for m in mesures)
    faits = sum(m['faits_absents_attendus'] for m in mesures)
    pieces = sum(m['pieces_attendues'] for m in mesures)
    marqueurs = sorted(m['marqueurs'] for m in mesures)
    return {
        'cas': n, 'schema_respecte': part('schema_ok'), 'deposes': part('depose'), 'corps_vides': part('corps_vide'),
        'marqueurs_moyenne': round(sum(marqueurs) / n, 2), 'marqueurs_mediane': marqueurs[n // 2], 'marqueurs_max': marqueurs[-1],
        'courriels_plus_de_2_marqueurs': sum(1 for m in mesures if m['marqueurs'] > MARQUEURS_MAX),
        'marqueurs_de_position': sum(m['marqueurs_position'] for m in mesures),
        'propositions_a_confirmer': sum(m.get('propositions_a_confirmer', 0) for m in mesures),
        'autres_crochets': sum(m['autres_crochets'] for m in mesures),
        'signatures': sum(m.get('signatures', 0) for m in mesures),
        'controle_favorable': sum(1 for m in mesures if m.get('controle') == 'favorable'),
        'controle_defavorable': sum(1 for m in mesures if m.get('controle') == 'defavorable'),
        'variantes_attendues': pos, 'variantes_presentes': sum(m['variantes_presentes'] for m in mesures),
        'part_variantes_presentes': round(sum(m['variantes_presentes'] for m in mesures) / pos, 3) if pos else None,
        'blocs_superflus': sum(m['blocs_superflus'] for m in mesures), 'blocs_invalides': sum(m['blocs_invalides'] for m in mesures),
        'faits_absents_attendus': faits, 'faits_absents_signales': sum(m['faits_absents_signales'] for m in mesures),
        'pieces_attendues': pieces, 'pieces_attendues_citees': sum(m['pieces_attendues_citees'] for m in mesures),
        'part_pieces_justes': part('pieces_justes'), 'pieces_inventees': sum(len(m['pieces_inventees']) for m in mesures),
        'elements_interdits': sum(m['nb_interdits'] for m in mesures), 'courriels_avec_interdit': sum(1 for m in mesures if m['nb_interdits']),
        'decisions_violees': sum(len(m['decisions_violees']) for m in mesures),
        'regle_respectee': part('regle_respectee'),
    }


# ------------------------------------------------------------------------------------------------------------------ garde réseau
@contextlib.contextmanager
def boucle_locale_seulement():
    """Toute connexion hors de 127.0.0.1 / ::1 est refusée AVANT d'être tentée (rédacteur et juge locaux, aucun service externe)."""
    original, original_ex, original_create = socket.socket.connect, socket.socket.connect_ex, socket.create_connection

    def hote(adresse):
        return str(adresse[0] if isinstance(adresse, tuple) else adresse)

    def verifier(adresse):
        if isinstance(adresse, tuple) and hote(adresse) not in BOUCLE_LOCALE:
            raise OSError('evaluation_redaction : connexion refusée hors de la boucle locale (%s)' % hote(adresse))

    def connect(self, adresse):
        verifier(adresse)
        return original(self, adresse)

    def connect_ex(self, adresse):
        verifier(adresse)
        return original_ex(self, adresse)

    def create_connection(adresse, *args, **kwargs):
        verifier(adresse)
        return original_create(adresse, *args, **kwargs)

    socket.socket.connect, socket.socket.connect_ex, socket.create_connection = connect, connect_ex, create_connection
    try:
        yield
    finally:
        socket.socket.connect, socket.socket.connect_ex, socket.create_connection = original, original_ex, original_create


def verifier_url_locale(url):
    from urllib.parse import urlsplit
    if (urlsplit(url).hostname or '') not in BOUCLE_LOCALE:
        raise SystemExit('ARRÊT : le modèle doit être local (127.0.0.1), pas %s' % url)
    return url


# ------------------------------------------------------------------------------------------------------------------ rédaction
class BoiteSimulee:
    """Messagerie IMAP simulée : le courriel du cas en lecture, les brouillons déposés gardés en mémoire (rien n'est envoyé)."""
    validity = '7'

    def __init__(self, *args, **kwargs):
        self.appended = []

    def fetch(self, folder, uid, headers_only=False):
        if folder == 'Drafts':   # relecture d'un brouillon déposé (maildraft5613.verify)
            from agent.mailbox import Mail
            return Mail(str(uid), '7', 'Drafts', {'\\Draft'}, datetime.now(timezone.utc), BoiteSimulee.deposes[int(uid) - 1])
        return BoiteSimulee.courant

    def search(self, folder, *criteres):
        valeur = str(criteres[-1]).strip('"') if criteres else ''
        return [str(i + 1) for i, d in enumerate(BoiteSimulee.deposes) if str(d.get('Message-ID', '')) == valeur] if folder == 'Drafts' else []

    def thread(self, *args, **kwargs):
        return []

    def preflight(self, *args, **kwargs):
        return ''

    def existing_draft(self, *args, **kwargs):
        return False

    def find_own_draft(self, draft_mid):
        return any(str(d['Message-ID']) == str(draft_mid) for d in BoiteSimulee.deposes)

    def append_draft(self, message):
        BoiteSimulee.deposes.append(message)

    def verify_draft(self, draft):
        return {'folder': 'Drafts', 'uid': str(len(BoiteSimulee.deposes)), 'uidvalidity': '7', 'verified_at': datetime.now(timezone.utc).isoformat()}

    def search_header(self, folder, name, value):
        return [str(i + 1) for i, d in enumerate(BoiteSimulee.deposes) if str(d.get(name, '')) == str(value)]

    def close(self):
        pass

    courant = None
    deposes = []


def _texte_message(message):
    try:
        partie = message.get_body(preferencelist=('plain',))
        return (partie.get_content() if partie is not None else '').replace('\r\n', '\n').strip()
    except Exception:   # noqa: BLE001
        return ''


def configuration(base, dossiers, modele, url):
    matters = base / 'matters.json'
    matters.write_text(json.dumps([{**d, 'aliases': d.get('aliases', []), 'references': d.get('references', [])} for d in dossiers],
                                  ensure_ascii=False), encoding='utf-8')
    (base / 'state').mkdir(exist_ok=True)
    (base / 'nextcloud').mkdir(exist_ok=True)
    return {'mode': 'drafts', 'state_dir': str(base / 'state'), 'matters_file': str(matters),
            'mail': {'host': 'mail.example.test', 'username': 'cabinet@example.test', 'own_addresses': ['cabinet@example.test'],
                     'inbox': 'INBOX', 'sent': 'Sent', 'drafts': 'Drafts', 'from_name': 'Maître Exemple',
                     'from_address': 'cabinet@example.test', 'signature': 'Maître Exemple\nCabinet Exemple, Valbrune'},
            'nextcloud': {'roots': ['/Dossiers'], 'local_path': str(base / 'nextcloud')}, 'documents': {},
            'ollama': {'url': url, 'model': modele, 'timeout_seconds': 900, 'max_context_chars': 65000, 'keep_alive': '30m'},
            'calendar': {'urls': [], 'timezone': 'Europe/Paris', 'notice_hours': 0}, 'max_messages_per_run': 1}


def _mail_du_cas(agent_mailbox, cas, uid):
    from email.message import EmailMessage
    m = EmailMessage()
    m['From'] = cas['courriel']['de']
    m['To'] = 'cabinet@example.test'
    m['Subject'] = cas['courriel']['objet']
    m['Message-ID'] = '<eval5695-%s-%s@example.test>' % (cas['id'].lower(), uid)
    m['Date'] = datetime.now(timezone.utc).strftime('%a, %d %b %Y %H:%M:%S +0000')
    m.set_content(cas['courriel']['texte'])
    return agent_mailbox.Mail(uid, '7', 'INBOX', set(), datetime.now(timezone.utc), m)


def _sources_du_cas(cas):
    return [{'id': p['id'], 'kind': 'document', 'source_kind': 'document', 'path': '/Dossiers/' + p['fichier'], 'etag': 'eval',
             'modified': '2026-11-01', 'excerpt': p['texte'], 'score': 1.0, 'metadata': {}, 'partial': False} for p in cas.get('pieces', [])]


class IndexDuCas:
    """Index des pièces simulé : rend les pièces du cas (mêmes sources pour les deux versions mesurées)."""

    def __init__(self, cas):
        self.cas = cas

    def sources(self, *args, **kwargs):
        docs = _sources_du_cas(self.cas)
        return docs, {'total_files': len(docs), 'pending_index': 0, 'indexed_files': len(docs), 'selected_files': len(docs),
                      'not_exhaustive': False, 'failed': [], 'retrieval': 'evaluation5695'}

    def __getattr__(self, name):
        raise AttributeError(name)


def rediger_un(desk, cas, dossiers, uid, patch):
    """Un cas par le chemin réel de l'arbre importé. Rend la sortie brute (corps et objet déposés, résultat, durées)."""
    from agent import intelligence, mailbox as agent_mailbox, societe5688, juridique5649, memoire5651
    from agent.state import State
    BoiteSimulee.deposes = []
    sortie = {'cas': cas['id'], 'schema_ok': False, 'depose': False, 'corps': '', 'objet': '', 'erreur': '', 'resultat': {},
              'sources_fournies': [p['id'] for p in cas.get('pieces', [])], 'sources_citees': [], 'a_completer': []}
    debut = time.monotonic()
    avec_dossier = bool(cas.get('dossier'))
    for d in cas.get('decisions', []):
        memoire5651.save_decision(desk, cas['dossier'], 'avocat@example.test', {'kind': d['type'], 'texte': d['texte']})
    if 'presentation_cabinet' in cas:
        from agent import reponses5671
        desk.setting(getattr(reponses5671, 'PRESENTATION', 'reponses5671:presentation'), cas.get('presentation_cabinet', ''))
    neutres = [patch.object(societe5688, 'avant', lambda *a, **k: {'societes': [], 'canal': 'courriel', 'propositions': []}),
               patch.object(juridique5649, 'is_legal_question', lambda *a, **k: False),
               patch.object(intelligence, 'source_index', lambda *a, **k: IndexDuCas(cas))]
    key, vu = None, {}
    try:
        with contextlib.ExitStack() as pile:
            for p in neutres:
                pile.enter_context(p)
            if cas.get('chemin') == 'courriel_nouveau':
                from agent import maildraft5613, docrequest520
                ctx = {'dossier': cas['dossier'], 'client': next((d['client_name'] for d in dossiers if d['id'] == cas['dossier']), ''),
                       'fichiers': [{'fichier': p['fichier'], 'modifie': '2026-11-01'} for p in cas.get('pieces', [])],
                       'extraits': [{'source': p['id'], 'fichier': p['fichier'], 'date': '2026-11-01', 'nature': 'document', 'texte': p['texte']}
                                    for p in cas.get('pieces', [])],
                       'courriels': [], 'agenda': [], 'faits': [], 'pieces_jointes': []}
                pile.enter_context(patch.object(docrequest520, 'context', lambda *a, **k: (dict(ctx), [])))
                boite = BoiteSimulee()
                rendu = maildraft5613.perform(desk, {'instruction': cas['consigne'], 'matter': cas['dossier']}, box=boite)
                sortie.update(schema_ok=True, resultat={k: v for k, v in rendu.items() if k not in ('mime', 'verification')},
                              sources_citees=list(rendu.get('sources') or []), a_completer=list(rendu.get('to_complete') or []))
            else:
                mail = _mail_du_cas(agent_mailbox, cas, uid)
                BoiteSimulee.courant = mail
                account = desk.c['mail']['username'] + '@' + desk.c['mail']['host']
                key = mail.key(account)
                report = {'status': 'review', 'matter': cas['dossier'] or '', 'source_mailbox': 'INBOX', 'source_uid': uid,
                          'reply_recipients': [cas['courriel']['de']], 'recipient_role': cas['courriel'].get('role', ''),
                          'sender': cas['courriel']['de'], 'subject': cas['courriel']['objet'],
                          'manual_request': {'by': 'avocat@example.test', 'at': desk.now(), 'instruction': cas.get('consigne', '')}}
                State(desk.c['state_dir']).report(key, report)
                pile.enter_context(patch.object(intelligence, 'Mailbox', BoiteSimulee))
                args = {'key': key, 'origine': 'boite5625', 'instruction': cas.get('consigne', '')}
                if avec_dossier:
                    args['matter'] = cas['dossier']
                else:
                    args['sans_dossier'] = 'oui'
                deposer = intelligence.deposit_draft

                def deposit_draft(d, a):   # projet relu juste avant le dépôt (la ligne est effacée une fois le brouillon déposé)
                    ligne = d.db.execute('SELECT data FROM manual_drafts WHERE mail_key=?', (a.get('key', ''),)).fetchone()
                    if ligne:
                        vu['data'] = json.loads(ligne[0])
                    vu['a_completer'] = a.get('a_completer') == 'oui'
                    return deposer(d, a)
                pile.enter_context(patch.object(intelligence, 'deposit_draft', deposit_draft))
                rendu = intelligence.prepare_reply(desk, args)
                ligne = desk.db.execute('SELECT data FROM manual_drafts WHERE mail_key=?', (key,)).fetchone()
                data = json.loads(ligne[0]) if ligne else vu.get('data', {})
                resultat = data.get('result') or {}
                sortie['depot_a_completer'] = bool(vu.get('a_completer'))
                if data.get('sources'):   # sources réellement transmises au rédacteur (courriel reçu, pièces, mémoire validée)
                    sortie['sources_fournies'] = [str(s.get('id')) for s in data['sources'] if isinstance(s, dict)]
                sortie['verification'] = data.get('verification')
                sortie.update(schema_ok=True, resultat=resultat, sources_citees=list(resultat.get('source_ids') or []),
                              a_completer=list(resultat.get('a_completer') or []), rendu={k: v for k, v in rendu.items() if isinstance(v, (str, bool, int))})
                if not BoiteSimulee.deposes:
                    sortie['corps'] = str(resultat.get('body') or '')   # non déposé : l'avocat lit le projet dans AxiorHub
        if BoiteSimulee.deposes:
            message = BoiteSimulee.deposes[-1]
            sortie.update(depose=True, objet=str(message.get('Subject', '')), corps=_texte_message(message),
                          entetes={k: str(v) for k, v in message.items() if k.lower().startswith('x-axiorhub')})
    except Exception as erreur:   # noqa: BLE001 — l'échec fait partie de la mesure (schéma invalide, contexte, modèle absent…)
        code = str(erreur) or type(erreur).__name__
        sortie['erreur'] = code[:300]
        sortie['schema_ok'] = not re.search(r'json_ia_invalide|schema|SchemaError', code + type(erreur).__name__, re.I)
        sortie.update(_projet_non_depose(desk, cas, key, vu))   # dépôt refusé : le projet reste lisible dans AxiorHub, il est mesuré tel quel
    sortie['duree_s'] = round(time.monotonic() - debut, 1)
    return sortie


def _projet_non_depose(desk, cas, key, vu):
    """Projet resté dans AxiorHub après un refus de dépôt (par exemple plus de 2 marqueurs) : corps et sources relus dans la base."""
    try:
        if cas.get('chemin') == 'courriel_nouveau':
            ligne = desk.db.execute('SELECT result FROM maildraft5613 ORDER BY updated DESC LIMIT 1').fetchone()
            data = json.loads(ligne[0] or '{}') if ligne else {}
            return {'corps': str(data.get('corps') or ''), 'resultat': data} if data.get('corps') else {}
        ligne = desk.db.execute('SELECT data FROM manual_drafts WHERE mail_key=?', (key or '',)).fetchone()
        data = json.loads(ligne[0]) if ligne else vu.get('data', {})
        resultat = data.get('result') or {}
        if not resultat.get('body'):
            return {}
        return {'corps': str(resultat['body']), 'resultat': resultat, 'sources_citees': list(resultat.get('source_ids') or []),
                'a_completer': list(resultat.get('a_completer') or [])}
    except Exception:   # noqa: BLE001
        return {}


def rediger(arbre, etiquette, sortie_dir, cas_choisis=None, modele=MODELE, url=JUGE_URL, reprendre=True):
    """Fait passer les cas par l'arbre ``arbre`` (dans CE processus : à lancer dans un sous-processus par version)."""
    verifier_url_locale(url)
    arbre = Path(arbre).resolve()
    sys.path.insert(0, str(arbre))
    import agent
    if Path(agent.__file__).resolve().parent != arbre / 'agent':
        raise SystemExit('ARRÊT : le paquet agent importé ne vient pas de %s' % arbre)
    from unittest.mock import patch
    from agent.desk import Desk
    data = charger_cas()
    fichier = Path(sortie_dir) / ('redaction-%s.jsonl' % etiquette)
    faits = set()
    if reprendre and fichier.exists():
        faits = {json.loads(l)['cas'] for l in fichier.read_text(encoding='utf-8').splitlines() if l.strip()}
    with boucle_locale_seulement(), tempfile.TemporaryDirectory(prefix='eval5695-', ignore_cleanup_errors=True) as tmp:
        for n, cas in enumerate(data['cas'], 1):
            if (cas_choisis and cas['id'] not in cas_choisis) or cas['id'] in faits:
                continue
            base = Path(tmp) / cas['id']
            base.mkdir()
            desk = Desk(configuration(base, data['dossiers'], modele, url))
            try:
                out = rediger_un(desk, cas, data['dossiers'], str(n), patch)
            finally:
                desk.db.close()
            out.update(version=getattr(agent, '__version__', ''), etiquette=etiquette, modele=modele, date=datetime.now(timezone.utc).isoformat())
            with fichier.open('a', encoding='utf-8') as f:
                f.write(json.dumps(out, ensure_ascii=False, default=str) + '\n')
            print('%s %s : %s, %s s%s' % (etiquette, cas['id'], 'déposé' if out['depose'] else 'non déposé', out['duree_s'],
                                           (' — ' + out['erreur']) if out['erreur'] else ''), flush=True)
    return fichier


# ------------------------------------------------------------------------------------------------------------------ juge
def environnement_juge(base=None):
    """Environnement du sous-processus DeepEval : télémétrie et collecte coupées, clés externes retirées, Ollama local imposé."""
    env = {k: v for k, v in (os.environ if base is None else base).items() if k not in ENV_RETIRES}
    env.update(ENV_JUGE)
    return env


def importer_deepeval():
    """Refuse d'importer DeepEval si la télémétrie n'est pas coupée dans CE processus ; rend None si DeepEval est absent."""
    manquants = [k for k, v in ENV_JUGE.items() if os.environ.get(k) != v]
    if manquants:
        raise SystemExit('ARRÊT : DeepEval ne peut pas être importé sans ces réglages : ' + ', '.join(manquants))
    presents = [k for k in ENV_RETIRES if os.environ.get(k)]
    if presents:
        raise SystemExit('ARRÊT : clés ou relais externes présents dans l’environnement du juge : ' + ', '.join(presents))
    try:
        import deepeval   # noqa: F401
        from deepeval.telemetry import telemetry_opt_out
    except ImportError:
        return None
    if not telemetry_opt_out():
        raise SystemExit('ARRÊT : la télémétrie de DeepEval reste active malgré DEEPEVAL_TELEMETRY_OPT_OUT')
    return deepeval


def ollama_disponible(url=JUGE_URL, modele=MODELE, ouvrir=None):
    """(vrai, motif) : le serveur local répond et le modèle juge est installé."""
    import urllib.request
    verifier_url_locale(url)
    ouvrir = ouvrir or (lambda u: urllib.request.urlopen(u, timeout=5).read())
    try:
        tags = json.loads(ouvrir(url.rstrip('/') + '/api/tags'))
    except Exception as erreur:   # noqa: BLE001
        return False, 'Ollama injoignable sur %s (%s)' % (url, type(erreur).__name__)
    noms = {m.get('name') for m in tags.get('models', [])}
    if modele not in noms:
        return False, 'modèle juge %s absent d’Ollama' % modele
    return True, ''


def juger_dans_ce_processus(sortie_dir, etiquette, modele=MODELE, url=JUGE_URL, cas_choisis=None):
    """Exécuté par l'interpréteur de l'environnement DeepEval (sous-processus lancé par ``juger``)."""
    deepeval = importer_deepeval()
    if deepeval is None:
        print('DeepEval absent : partie DeepEval ignorée.')
        return None
    ok, motif = ollama_disponible(url, modele)
    if not ok:
        print(motif + ' : partie DeepEval ignorée.')
        return None
    from deepeval.models import OllamaModel
    from deepeval.metrics import FaithfulnessMetric, AnswerRelevancyMetric, GEval
    from deepeval.test_case import LLMTestCase, SingleTurnParams
    data = charger_cas()
    par_id = {c['id']: c for c in data['cas']}
    sorties = [json.loads(l) for l in (Path(sortie_dir) / ('redaction-%s.jsonl' % etiquette)).read_text(encoding='utf-8').splitlines() if l.strip()]
    fichier = Path(sortie_dir) / ('juge-%s.jsonl' % etiquette)
    faits = {json.loads(l)['cas'] for l in fichier.read_text(encoding='utf-8').splitlines() if l.strip()} if fichier.exists() else set()
    juge = OllamaModel(model=modele, base_url=url, temperature=0, generation_kwargs={'num_ctx': 16384, 'seed': 5695})
    with boucle_locale_seulement():
        for s in sorties:
            cas = par_id.get(s['cas'])
            if not cas or s['cas'] in faits or (cas_choisis and s['cas'] not in cas_choisis):
                continue
            ligne = {'cas': s['cas'], 'etiquette': etiquette, 'juge': modele}
            corps = str(s.get('corps') or '').strip()
            if not corps:
                ligne.update(faithfulness=None, answer_relevancy=None, livrable_termine=None, motif='corps vide : non jugé')
            else:
                entree = ('Courriel reçu — objet : %s\n%s' % (cas['courriel']['objet'], cas['courriel']['texte'])) if cas.get('courriel') else ''
                entree += ('\n\nConsigne de l’avocat : ' + cas['consigne']) if cas.get('consigne') else ''
                contexte = ([entree] + ['[%s] %s' % (p['id'], p['texte']) for p in cas.get('pieces', [])] +
                            ['Décision validée de l’avocat : ' + d['texte'] for d in cas.get('decisions', [])] +
                            (['Présentation du cabinet : ' + (cas.get('presentation_cabinet') or 'non renseignée')] if 'presentation_cabinet' in cas else []))
                tc = LLMTestCase(input=entree.strip(), actual_output=corps, retrieval_context=contexte, context=contexte)
                metriques = {
                    'faithfulness': FaithfulnessMetric(model=juge, eval_mode='llm', include_reason=False, async_mode=False, truths_extraction_limit=12),
                    'answer_relevancy': AnswerRelevancyMetric(model=juge, include_reason=False, async_mode=False),
                    'livrable_termine': GEval(name='Livrable terminé', evaluation_steps=LIVRABLE_ETAPES, model=juge, async_mode=False,
                                              evaluation_params=[SingleTurnParams.INPUT, SingleTurnParams.ACTUAL_OUTPUT, SingleTurnParams.CONTEXT]),
                }
                for nom, metrique in metriques.items():
                    debut = time.monotonic()
                    try:
                        metrique.measure(tc)
                        ligne[nom] = round(float(metrique.score), 3)
                    except Exception as erreur:   # noqa: BLE001 — un juge local peut rendre un JSON invalide : noté, jamais converti en score
                        ligne[nom] = None
                        ligne[nom + '_erreur'] = (type(erreur).__name__ + ' : ' + str(erreur))[:200]
                    ligne[nom + '_duree_s'] = round(time.monotonic() - debut, 1)
            with fichier.open('a', encoding='utf-8') as f:
                f.write(json.dumps(ligne, ensure_ascii=False) + '\n')
            print('juge %s %s : %s' % (etiquette, s['cas'], {k: ligne.get(k) for k in ('faithfulness', 'answer_relevancy', 'livrable_termine')}), flush=True)
    return fichier


def juger(sortie_dir, etiquette, python_juge, modele=MODELE, url=JUGE_URL, cas_choisis=None):
    """Lance le juge dans l'interpréteur de l'environnement DeepEval, avec l'environnement filtré ; s'ignore proprement si absent."""
    verifier_url_locale(url)
    if not python_juge or not Path(python_juge).exists():
        print('Interpréteur DeepEval absent (%s) : partie DeepEval ignorée.' % (python_juge or 'non indiqué'))
        return None
    commande = [str(python_juge), str(Path(__file__).resolve()), '_juger', '--sortie', str(sortie_dir), '--etiquette', etiquette,
                '--modele', modele, '--url', url] + (['--cas', ','.join(cas_choisis)] if cas_choisis else [])
    return subprocess.call(commande, env=environnement_juge(), cwd=str(sortie_dir))


# ------------------------------------------------------------------------------------------------------------------ rapport
def _lire(chemin):
    return [json.loads(l) for l in Path(chemin).read_text(encoding='utf-8').splitlines() if l.strip()] if Path(chemin).exists() else []


def _moyenne(valeurs):
    valeurs = [v for v in valeurs if isinstance(v, (int, float))]
    return round(sum(valeurs) / len(valeurs), 3) if valeurs else None


def rapport(sortie_dir, etiquettes):
    data = charger_cas()
    par_id = {c['id']: c for c in data['cas']}
    versions = {}
    for etiquette in etiquettes:
        sorties = {s['cas']: s for s in _lire(Path(sortie_dir) / ('redaction-%s.jsonl' % etiquette))}
        mesures = [analyser(par_id[k], s, data['dossiers']) for k, s in sorted(sorties.items()) if k in par_id]
        juges = {j['cas']: j for j in _lire(Path(sortie_dir) / ('juge-%s.jsonl' % etiquette))}
        for m in mesures:
            j = juges.get(m['cas'], {})
            m.update({k: j.get(k) for k in ('faithfulness', 'answer_relevancy', 'livrable_termine')})
            m['duree_redaction_s'] = sorties[m['cas']].get('duree_s')
            m['duree_juge_s'] = round(sum(j.get(k + '_duree_s') or 0 for k in ('faithfulness', 'answer_relevancy', 'livrable_termine')), 1) if j else None
        s = synthese(mesures)
        if mesures:
            s.update({k: _moyenne(m.get(k) for m in mesures) for k in ('faithfulness', 'answer_relevancy', 'livrable_termine')})
            s['juges'] = sum(1 for m in mesures if m.get('livrable_termine') is not None)
            durees = sorted(m['duree_redaction_s'] for m in mesures if m.get('duree_redaction_s') is not None)
            s['duree_redaction_totale_s'] = round(sum(durees), 1)
            s['duree_redaction_mediane_s'] = durees[len(durees) // 2] if durees else None
            s['duree_juge_totale_s'] = round(sum(m['duree_juge_s'] or 0 for m in mesures), 1)
            s['erreurs'] = sorted({m['erreur'] for m in mesures if m['erreur']})
            s['version_arbre'] = next((x.get('version') for x in sorties.values()), '')
        versions[etiquette] = {'synthese': s, 'cas': mesures}
    rapport_json = {'date': datetime.now(timezone.utc).isoformat(), 'modele': MODELE, 'juge': MODELE + ' (Ollama local)', 'versions': versions}
    Path(sortie_dir, 'rapport.json').write_text(json.dumps(rapport_json, ensure_ascii=False, indent=2), encoding='utf-8')
    Path(sortie_dir, 'rapport.md').write_text(tableau(versions), encoding='utf-8')
    return rapport_json


LIGNES = [('Cas mesurés', 'cas'), ('Schéma respecté (part)', 'schema_respecte'), ('Brouillons déposés (part)', 'deposes'),
          ('Corps vides (part)', 'corps_vides'), ('Marqueurs par courriel (moyenne)', 'marqueurs_moyenne'),
          ('Marqueurs par courriel (médiane)', 'marqueurs_mediane'), ('Marqueurs par courriel (maximum)', 'marqueurs_max'),
          ('Courriels avec plus de 2 marqueurs', 'courriels_plus_de_2_marqueurs'), ('Marqueurs portant sur une position', 'marqueurs_de_position'),
          ('Autres espaces à remplir entre crochets (total)', 'autres_crochets'),
          ('Signatures ou identités à remplir (total)', 'signatures'),
          ('Contrôle indépendant tout favorable (courriels)', 'controle_favorable'),
          ('Contrôle indépendant tout défavorable (courriels)', 'controle_defavorable'),
          ('Variantes attendues présentes', 'variantes_presentes'), ('Variantes attendues (total)', 'variantes_attendues'),
          ('Blocs de variantes superflus', 'blocs_superflus'), ('Blocs de variantes invalides', 'blocs_invalides'),
          ('« Proposition à confirmer » (total)', 'propositions_a_confirmer'),
          ('Faits absents signalés / attendus', 'faits_absents_signales'), ('Pièces attendues citées', 'pieces_attendues_citees'),
          ('Pièces justes (part des courriels)', 'part_pieces_justes'), ('Pièces inventées', 'pieces_inventees'),
          ('Éléments interdits (total)', 'elements_interdits'), ('Courriels avec élément interdit', 'courriels_avec_interdit'),
          ('Décisions de l’avocat contredites', 'decisions_violees'), ('Règle complète respectée (part)', 'regle_respectee'),
          ('DeepEval fidélité aux sources (moyenne)', 'faithfulness'), ('DeepEval pertinence de la réponse (moyenne)', 'answer_relevancy'),
          ('DeepEval « livrable terminé » (moyenne)', 'livrable_termine'), ('Cas jugés par DeepEval', 'juges'),
          ('Durée de rédaction totale (s)', 'duree_redaction_totale_s'), ('Durée de rédaction médiane par cas (s)', 'duree_redaction_mediane_s'),
          ('Durée du juge totale (s)', 'duree_juge_totale_s')]


def tableau(versions):
    noms = list(versions)
    out = ['# Évaluation des brouillons de courriels', '', 'Rédacteur et juge : %s par Ollama local. Cas fictifs : tests/data/redaction5695.' % MODELE, '',
           '| Mesure | ' + ' | '.join(noms) + ' |', '|---|' + '---|' * len(noms)]
    for libelle, cle in LIGNES:
        valeurs = []
        for n in noms:
            s = versions[n]['synthese']
            v = s.get(cle)
            if cle == 'faits_absents_signales':
                v = '%s / %s' % (s.get('faits_absents_signales'), s.get('faits_absents_attendus'))
            elif cle == 'pieces_attendues_citees':
                v = '%s / %s' % (s.get('pieces_attendues_citees'), s.get('pieces_attendues'))
            valeurs.append('—' if v is None else str(v))
        out.append('| %s | %s |' % (libelle, ' | '.join(valeurs)))
    out += ['', '## Par cas', '', '| Cas | Catégorie | ' + ' | '.join('%s dépôt / marqueurs / variantes / propositions / interdits / livrable' % n for n in noms) + ' |',
            '|---|---|' + '---|' * len(noms)]
    ids = sorted({m['cas'] for n in noms for m in versions[n]['cas']})
    for i in ids:
        cells, cat = [], ''
        for n in noms:
            m = next((x for x in versions[n]['cas'] if x['cas'] == i), None)
            if m is None:
                cells.append('—')
                continue
            cat = m['categorie']
            cells.append('%s / %d / %d sur %d / %d / %d / %s%s' % ('déposé' if m['depose'] else 'non déposé', m['marqueurs'], m['variantes_presentes'],
                                                             m['positions_attendues'], m.get('propositions_a_confirmer', 0), m['nb_interdits'],
                                                       '—' if m.get('livrable_termine') is None else m['livrable_termine'], ' (erreur)' if m['erreur'] else ''))
        out.append('| %s | %s | %s |' % (i, cat, ' | '.join(cells)))
    return '\n'.join(out) + '\n'


# ------------------------------------------------------------------------------------------------------------------ ligne de commande
def main(argv=None):
    p = argparse.ArgumentParser(description='Évaluation des brouillons de courriels (poste de développement uniquement).')
    p.add_argument('commande', choices=['rediger', 'juger', 'rapport', '_rediger', '_juger'])
    p.add_argument('--arbre', help='arbre AxiorHub à mesurer (contient agent/)')
    p.add_argument('--etiquette', help='nom de la mesure, par exemple 5.6.94')
    p.add_argument('--etiquettes', help='mesures à comparer, séparées par des virgules')
    p.add_argument('--sortie', default='evaluation-redaction-resultats', help='dossier des résultats (jamais dans agent/)')
    p.add_argument('--python-juge', default=os.environ.get('AXIORHUB_EVAL_PYTHON', ''), help='interpréteur de l’environnement DeepEval')
    p.add_argument('--modele', default=MODELE)
    p.add_argument('--url', default=JUGE_URL)
    p.add_argument('--cas', default='', help='identifiants à mesurer, séparés par des virgules')
    a = p.parse_args(argv)
    cas = [x for x in a.cas.split(',') if x] or None
    sortie = Path(a.sortie).resolve()
    if 'agent' in sortie.parts:
        raise SystemExit('ARRÊT : résultats jamais dans agent/')
    sortie.mkdir(parents=True, exist_ok=True)
    if a.commande == 'rediger':   # un sous-processus par version : aucun mélange de deux paquets agent dans un même interpréteur
        env = {k: v for k, v in os.environ.items() if k not in ENV_RETIRES}
        env.update({'PYTHONUTF8': '1', 'PYTHONIOENCODING': 'utf-8', 'OLLAMA_HOST': '127.0.0.1:11434'})
        commande = [sys.executable, str(Path(__file__).resolve()), '_rediger', '--arbre', a.arbre, '--etiquette', a.etiquette,
                    '--sortie', str(sortie), '--modele', a.modele, '--url', a.url] + (['--cas', a.cas] if cas else [])
        return subprocess.call(commande, env=env, cwd=str(sortie))
    if a.commande == '_rediger':
        rediger(a.arbre, a.etiquette, sortie, cas, a.modele, a.url)
        return 0
    if a.commande == 'juger':
        return juger(sortie, a.etiquette, a.python_juge, a.modele, a.url, cas) or 0
    if a.commande == '_juger':
        juger_dans_ce_processus(sortie, a.etiquette, a.modele, a.url, cas)
        return 0
    r = rapport(sortie, [x for x in (a.etiquettes or '').split(',') if x])
    print(Path(sortie, 'rapport.md').read_text(encoding='utf-8'))
    return 0 if r['versions'] else 1


if __name__ == '__main__':
    sys.exit(main())
