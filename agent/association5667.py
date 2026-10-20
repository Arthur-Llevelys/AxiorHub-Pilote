"""5.6.67 (point 2) : rattachement des courriels appris de ce que le cabinet a déjà établi.

Pour chaque dossier, en mémoire seulement (la configuration des dossiers n'est jamais modifiée) :
- ``learned_references`` : numéros RG connus du dossier (procédures de la fiche, avis de procédure analysés), sous leurs écritures
  usuelles (« 24/01234 », « 24-01234 ») ; un RG partagé par deux dossiers n'est retenu pour aucun ;
- ``learned_names`` : noms des parties de la fiche (et leurs autres mentions) propres à CE dossier ; un nom présent dans plusieurs
  dossiers (même banque adverse, par exemple) ne sert pas à les distinguer ;
- ``learned_senders`` : expéditeurs dont l'avocat a confirmé le rattachement à ce seul dossier.

Le rapprochement (``matcher.rank``) garde ses règles prudentes : un RG appris compte comme une référence explicite (rattachement
automatique s'il désigne un seul dossier, conflit sinon), un nom ou un expéditeur confirmé comme un indice ; le rôle du correspondant
n'est jamais deviné.
"""
from collections import defaultdict
import json
from pathlib import Path
import re
import sqlite3

from .common import fold

RG_RX = re.compile(r'(?<![\d/.-])(\d{2}|\d{4})\s*[/-]\s*(\d{3,6})(?!\d|[/.-]\d)')
RG_CONTEXT = re.compile(r'(?<![a-z])(?:rg|r\.g\.|repertoire general)(?![a-z])[^0-9]{0,12}((?:\d{2}|\d{4})\s*[/-]\s*\d{3,6})(?!\d|[/.-]\d)')
GENERIC = {'societe', 'monsieur', 'madame', 'maitre', 'sarl', 'sas', 'sasu', 'eurl', 'sci', 'snc', 'scp', 'selarl', 'association',
           'tribunal', 'judiciaire', 'commerce', 'cour', 'appel', 'banque', 'credit', 'caisse', 'mutuelle', 'assurance', 'assurances',
           'cabinet', 'avocat', 'avocats', 'client', 'groupe', 'france', 'mairie', 'commune', 'ville', 'etat', 'syndicat', 'copropriete',
           'conseil', 'agence', 'immobilier', 'immobiliere', 'holding', 'service', 'services', 'compagnie', 'entreprise', 'monsieur'}
MIN_NAME = 6


def rg_variants(value, in_text=False):
    """Écritures usuelles d'un numéro RG. ``in_text`` (objet ou texte d'un courriel) : seulement un numéro précédé de « RG » — jamais
    un fragment de date (« 10/2026 » dans « 20/10/2026 »)."""
    text = str(value or '')
    if in_text:
        text = ' '.join(RG_CONTEXT.findall(fold(text)))
    out = []
    for year, number in RG_RX.findall(text):
        out += ['%s/%s' % (year, number), '%s-%s' % (year, number)]
    return out


def _distinctive(name):
    words = [w for w in re.findall(r'[a-z0-9]+', fold(name)) if len(w) >= 4]
    return len(fold(name).strip()) >= MIN_NAME and any(w not in GENERIC for w in words)


def _rows(db, sql, params=()):
    try:
        return db.execute(sql, params).fetchall()
    except sqlite3.Error:
        return []


def learned(state_dir):
    """{dossier: {'references': [...], 'names': [...], 'senders': [...]}} lu en lecture seule dans la base du cabinet."""
    path = Path(state_dir) / 'desk.sqlite3'
    if not path.is_file():
        return {}
    db = sqlite3.connect('file:%s?mode=ro' % path.as_posix(), uri=True, timeout=3)
    try:
        refs, names, senders = defaultdict(set), defaultdict(set), defaultdict(set)
        for mid, rg in _rows(db, "SELECT matter,rg FROM d51_procedures WHERE rg<>'' AND statut='active' AND review NOT IN ('rejete','refuse','ecarte')"):
            refs[str(mid)].update(rg_variants(rg))
        for mid, result in _rows(db, "SELECT matter,result FROM notices440 WHERE status='done'"):
            try:
                rg = (json.loads(result or '{}') or {}).get('rg', '')
            except (ValueError, TypeError):
                rg = ''
            refs[str(mid)].update(rg_variants(rg))
        for mid, name in _rows(db, "SELECT matter,display_name FROM d51_parties WHERE status='active'"):
            if _distinctive(name):
                names[str(mid)].add(str(name).strip())
        for mid, alias in _rows(db, 'SELECT matter,alias FROM d51_aliases'):
            if _distinctive(alias):
                names[str(mid)].add(str(alias).strip())
        for mid, sender in _rows(db, "SELECT matter,sender FROM portfolio_mail_links WHERE status='confirmed' AND sender<>''"):
            senders[str(sender).strip().lower()].add(str(mid))
    finally:
        db.close()
    # un indice partagé par plusieurs dossiers ne les distingue pas
    ref_owner, name_owner = defaultdict(set), defaultdict(set)
    for mid, values in refs.items():
        for v in values:
            ref_owner[v].add(mid)
    for mid, values in names.items():
        for v in values:
            name_owner[fold(v)].add(mid)
    out = defaultdict(lambda: {'references': [], 'names': [], 'senders': []})
    for mid, values in refs.items():
        out[mid]['references'] = sorted(v for v in values if len(ref_owner[v]) == 1)
    for mid, values in names.items():
        out[mid]['names'] = sorted(v for v in values if len(name_owner[fold(v)]) == 1)
    for sender, mids in senders.items():
        if len(mids) == 1 and '@' in sender:
            out[next(iter(mids))]['senders'].append(sender)
    return dict(out)


def augment(config, matters):
    """Copies des dossiers enrichies (clés learned_*) ; en cas d'erreur, les dossiers d'origine, inchangés."""
    try:
        data = learned(config['state_dir'])
    except Exception:   # noqa: BLE001 — base indisponible : rapprochement habituel
        return matters
    if not data:
        return matters
    out = []
    for m in matters:
        extra = data.get(str(m.get('id')))
        if extra:
            m = {**m, 'learned_references': extra['references'], 'learned_names': extra['names'], 'learned_senders': extra['senders']}
        out.append(m)
    return out
