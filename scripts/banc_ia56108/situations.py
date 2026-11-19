"""5.6.108 (lot E) : situations fictives du banc d'essai : chargement et contrôle du schéma."""
import json
from pathlib import Path
import re

KINDS = ('prepare_reply', 'assistant_answer', 'prepare_legal_opinion', 'legal_research', 'prepare_document_project', 'attachment_review',
         'digest_pieces5632', 'prepare_hearing', 'verify_deliverable420', 'analyser_dossier5624', 'fiche_dossier5627', 'refresh_brief')
FAMILLES = ('courriel', 'question', 'consultation', 'acte', 'pieces', 'recherche', 'audience', 'controle')
LIVRABLES = ('courriel', 'reponse', 'consultation', 'acte', 'analyse_pieces', 'recherche', 'audience', 'controle')
PLACES = (4000, 8000, 16000, 32000)
DROITS = ('FR', 'DE', 'ES')
PIEGES = ('citation_adverse', 'texte_ancien', 'droit_etranger', 'consigne_cachee')
CHAMPS = ('id', 'famille', 'kind', 'consigne', 'dossier', 'droit', 'place', 'livrable', 'sources', 'attendus')


def valider(situation):
    """Liste des défauts d'une situation (vide si elle est valide)."""
    s, d = situation, []
    for champ in CHAMPS:
        if champ not in s:
            d.append('%s : champ %s absent' % (s.get('id', '?'), champ))
    if d:
        return d
    ident = s['id']
    if not re.fullmatch(r'[A-Z]{2,4}-\d{2}', str(ident)):
        d.append('%s : identifiant invalide' % ident)
    if s['famille'] not in FAMILLES:
        d.append('%s : famille inconnue' % ident)
    if s['kind'] not in KINDS:
        d.append('%s : type de travail inconnu %s' % (ident, s['kind']))
    if s['livrable'] not in LIVRABLES:
        d.append('%s : livrable inconnu' % ident)
    if s['place'] not in PLACES:
        d.append('%s : place de contexte hors de %s' % (ident, PLACES))
    if not str(s['consigne']).strip():
        d.append('%s : consigne vide' % ident)
    droit = s['droit']
    if not isinstance(droit, dict) or droit.get('code') not in DROITS or not droit.get('libelle'):
        d.append('%s : droit du dossier invalide' % ident)
    sources = s['sources']
    if not isinstance(sources, list):
        d.append('%s : sources invalides' % ident)
    else:
        vus = set()
        for x in sources:
            if not isinstance(x, dict) or not re.fullmatch(r'S\d+', str(x.get('id', ''))) or not x.get('titre') or not x.get('texte'):
                d.append('%s : source invalide' % ident)
            elif x['id'] in vus:
                d.append('%s : source en double %s' % (ident, x['id']))
            else:
                vus.add(x['id'])
        total = sum(len(str(x.get('texte', ''))) for x in sources if isinstance(x, dict))
        if total > s['place'] * 3 // 2:   # les sources tiennent dans la moitié de la place simulée (≈ 3 caractères par jeton)
            d.append('%s : sources trop longues pour la place simulée' % ident)
    att = s['attendus']
    if not isinstance(att, dict):
        return d + ['%s : attendus invalides' % ident]
    for m in att.get('manques') or []:
        if not m.get('champ') or not (m.get('mots') or m.get('motif')):
            d.append('%s : manque sans champ ni mots' % ident)
    for x in (att.get('mots_requis') or []) + (att.get('mots_interdits') or []):
        try:
            re.compile(x)
        except re.error:
            d.append('%s : expression invalide %r' % (ident, x))
    for p in s.get('pieges') or []:
        if p.get('type') not in PIEGES:
            d.append('%s : piège inconnu' % ident)
        if p.get('type') == 'consigne_cachee' and (not p.get('temoin') or not any(p['temoin'] in x.get('texte', '') for x in sources)):
            d.append('%s : consigne cachée sans témoin présent dans une pièce' % ident)
        if p.get('type') == 'citation_adverse' and not p.get('reference'):
            d.append('%s : citation adverse sans référence' % ident)
        if p.get('type') == 'droit_etranger' and droit.get('code') == 'FR':
            d.append('%s : piège « droit étranger » sur un dossier français' % ident)
    return d + valider_corpus(s)


CORPUS_TYPES = ('article', 'decision')
MENTION_FICTIVE = 'texte fictif pour l’essai'


def valider_corpus(s):
    """5.6.108 (lot R) : champ facultatif ``corpus`` (mode chaîne : connecteurs factices). Textes de loi et décisions inventés, chacun marqué
    « texte fictif pour l’essai », lien seulement sur un domaine ``example.test`` ; jamais présentés comme du droit réel."""
    ident, d = s.get('id', '?'), []
    corpus = s.get('corpus')
    if corpus is None:
        return d
    if not isinstance(corpus, list):
        return ['%s : corpus invalide' % ident]
    vus = set()
    for c in corpus:
        if not isinstance(c, dict) or not re.fullmatch(r'C\d+', str(c.get('id', ''))) or c.get('type') not in CORPUS_TYPES:
            d.append('%s : entrée de corpus invalide' % ident)
            continue
        if c['id'] in vus:
            d.append('%s : entrée de corpus en double %s' % (ident, c['id']))
        vus.add(c['id'])
        if not all(str(c.get(k) or '').strip() for k in ('reference', 'titre', 'texte', 'numero')) or (c['type'] == 'article' and not c.get('code')):
            d.append('%s : entrée de corpus incomplète %s' % (ident, c['id']))
        if MENTION_FICTIVE not in str(c.get('texte') or '').lower():
            d.append('%s : entrée de corpus sans la mention « texte fictif pour l’essai » %s' % (ident, c['id']))
        lien = str(c.get('lien') or '')
        if lien and not re.fullmatch(r'https://[a-z0-9.-]+\.example\.test(?:/[^\s]*)?', lien):
            d.append('%s : lien de corpus hors du domaine example.test %s' % (ident, c['id']))
    return d


def charger(chemin):
    data = json.loads(Path(chemin).read_text(encoding='utf-8'))
    situations = data['situations'] if isinstance(data, dict) else data
    defauts, vus = [], set()
    for s in situations:
        defauts += valider(s)
        if s.get('id') in vus:
            defauts.append('%s : identifiant en double' % s.get('id'))
        vus.add(s.get('id'))
    if defauts:
        raise ValueError('Situations invalides :\n' + '\n'.join(defauts[:40]))
    return situations
