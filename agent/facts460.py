"""Faits constants d'un dossier (AxiorHub 4.6.0).

Extraction déterministe (aucun modèle) de faits repérables dans le texte des pièces : numéro de RG,
juridiction, montants, dates d'actes, parties. Chaque fait est proposé avec sa source citée et un niveau
de confiance qui tient compte de la qualité du texte (pièces scannées). Rien n'est affirmé : l'avocat
valide, corrige ou refuse en un clic ; un fait refusé n'est plus jamais reproposé ni réutilisé.
"""
from datetime import date
import json
import re

from .common import Stop, digest, fold
from .legal_memory import (change_record, ensure_schema, memory_records, matter as _matter, upsert_record)
from .notices440 import DATE_NUM, DATE_TEXT, MONTHS, RG, fold_chars

FACT_TYPES = ('case_number', 'jurisdiction', 'amount', 'event', 'party')
TYPE_LABELS = {'case_number': 'Numéro de RG', 'jurisdiction': 'Juridiction', 'amount': 'Montant', 'event': 'Date clé',
               'party': 'Partie', 'claim': 'Demande', 'deadline': 'Échéance', 'completed_action': 'Acte accompli',
               'instruction': 'Instruction', 'negotiation': 'Négociation', 'planned_action': 'Action prévue',
               'open_question': 'Question ouverte', 'document': 'Pièce', 'other': 'Autre'}
JURISDICTION = re.compile(r"(?i)\b((?:tribunal\s+(?:de\s+commerce|judiciaire|paritaire\s+des\s+baux\s+ruraux|des\s+activit[ée]s\s+[ée]conomiques)|"
                          r"cour\s+d.appel|conseil\s+de\s+prud.hommes|juge\s+des\s+contentieux\s+de\s+la\s+protection)\s+(?:de\s+|d.)[A-ZÀ-Ý][\w'’\-]+(?:[\s\-][A-ZÀ-Ý][\w'’\-]+){0,2})")
AMOUNT = re.compile(r"(?<![\w.,])(\d{1,3}(?:[   .]\d{3})+(?:,\d{1,2})?|\d+(?:,\d{1,2})?)\s*(?:€|euros?\b|EUR\b)", re.I)
AMOUNT_CUE = re.compile(r"condamn|\bsomme|montant|principal|cr[ée]ance|\bprix|dommages|factur|paiement|r[ée]glement|provision|indemnit|solde|\bd[ûÛ]e?s?\b|\bdemande\s+(?:de|en)")
EVENT_CUES = (
    ('Jugement', r"jugement\s+(?:rendu\s+)?(?:du|le)"), ('Ordonnance', r"ordonnance\s+(?:rendue\s+)?(?:du|le)"),
    ('Arrêt', r"arr[êe]t\s+(?:rendu\s+)?(?:du|le)"), ('Assignation', r"assignation\s+(?:d[ée]livr[ée]e\s+)?(?:du|le)"),
    ('Mise en demeure', r"mise\s+en\s+demeure\s+(?:du|le)|mis\s+en\s+demeure\s+le"), ('Contrat', r"contrat\s+(?:a\s+[ée]t[ée]\s+)?(?:conclu\s+|sign[ée]\s+)?(?:du|le)"),
    ('Facture', r"facture\s+(?:n[°o]\s*\S+\s+)?(?:du|le)"), ('Signification', r"sign[ie]fi[ée]e?\s+le"),
)
PARTY = re.compile(r"\b(?:la\s+soci[ée]t[ée]\s+)?((?:SAS|SARL|SA|SASU|EURL|SCI|SELARL|SNC)\s+[A-ZÀ-Ý][\w'’\-&]*(?:\s+[A-ZÀ-Ý][\w'’\-&]*){0,3})")
PERSON = re.compile(r"\b(M\.|Monsieur|Madame|Mme)\s+([A-ZÀ-Ý][\w'’\-]+(?:\s+[A-ZÀ-Ý][\w'’\-]+){0,2})")
NOISE_PERSON = {'le', 'la', 'les'}
# 5.6.27 : une partie n'est proposée que si la pièce la désigne comme telle ; jamais un auxiliaire de justice, un magistrat ou un avocat.
PARTY_CUE = re.compile(r"requete de|demandeu|defendeu|defenderesse|appelant|intime|intervenant|contre\s*:|pour\s*:|entre\s*:|a l.encontre de|condamnation (?:solidaire )?de|"
                       r"assigne|cite a comparaitre|partie civile|prevenu|bailleur|preneur|vendeur|acquereur|employeur|salarie")
NOT_PARTY = re.compile(r"avocat|maitre|\bme\b|toque|barreau|huissier|commissaire|notaire|juge|greffi|president|conseiller|expert|mandataire judiciaire|"
                       r"ministere de|signifie|signification|domicile elu|postulant|plaidant|substitut|procureur")
PARTY_AFTER = re.compile(r"\b(demande|sollicite|reclame|assigne|conclut|requiert|forme appel|interjette|represente[e]? par son|prise en la personne)")
TRAILING = {'piece', 'pieces', 'date', 'identifiant', 'organisme', 'situations', 'situation', 'monsieur', 'madame', 'toque', 'avocats', 'avocat',
            'maitre', 'huissier', 'de', 'du', 'des', 'et', 'societe', 'adresse', 'ne', 'nee'}


def party_name(name):
    """Nom de partie nettoyé des mots parasites en fin (« Pièce », « Date », « Toque »…) ; '' si rien ne reste."""
    words = str(name or '').split()
    while words and fold(words[-1]).strip('.,;:') in TRAILING:
        words.pop()
    while words and fold(words[0]).strip('.,;:') in {'monsieur', 'madame', 'm', 'mme'} and len(words) > 1 and fold(words[1]).strip('.') in {'monsieur', 'madame'}:
        words.pop(0)
    return ' '.join(words)


def looks_like_party(folded, start, end, name):
    """Vrai seulement si la pièce désigne ce nom comme partie : un repère (« à la requête de », « contre : », « demandeur »…) dans la même
    phrase, moins de 220 caractères avant ; et ni le nom ni ses voisins immédiats sur la même ligne ne désignent un avocat, un auxiliaire de
    justice ou un magistrat."""
    line_start = folded.rfind(chr(10), 0, start) + 1
    line_end = folded.find(chr(10), end)
    near = folded[max(line_start, start - 28):min(line_end if line_end >= 0 else len(folded), end + 22)]
    if NOT_PARTY.search(fold(name)) or NOT_PARTY.search(near):
        return False
    after = re.split(r'[.;]\s', folded[end:end + 140], 1)[0]   # « La SAS X, représentée par…, demande la condamnation de… »
    if PARTY_AFTER.search(after):
        return True
    window = folded[max(0, start - 220):start]
    cues = list(PARTY_CUE.finditer(window))
    if not cues:
        return False
    between = window[cues[-1].end():]
    return not re.search(r'[.;]\s', between)


def text_quality(text):
    """0..1 : part de texte lisible. Une reconnaissance de caractères médiocre donne une confiance basse."""
    text = str(text or '')
    if len(text.strip()) < 40:
        return 0.0
    letters = sum(c.isalpha() or c.isdigit() or c in ' \n\t.,;:\'’-()€%/°' for c in text)
    symbols = len(re.findall(r"[¤¦~^`#¬|\\{}\[\]<>=*_@]", text)) / len(text)
    words = re.findall(r"[A-Za-zÀ-ÿ]{2,}", text)
    long_share = sum(1 for w in words if len(w) <= 18) / max(1, len(words))
    single = len(re.findall(r"(?<!\w)[A-Za-zÀ-ÿ](?!\w)", text)) / max(1, len(words))
    score = (letters / len(text)) * 0.55 + long_share * 0.3 + max(0.0, 1 - single * 2) * 0.15
    score -= min(0.5, symbols * 5)
    return round(max(0.0, min(1.0, score)), 3)


def _confidence(strong, quality):
    if quality < 0.55:
        return 'low'
    if strong and quality >= 0.85:
        return 'high'
    return 'medium' if quality >= 0.7 or strong else 'low'


def _clip(text, start, end, before=90, after=90):
    return re.sub(r'\s+', ' ', text[max(0, start - before):min(len(text), end + after)]).strip()


def _fr_amount(raw):
    clean = re.sub(r'[\s\u00a0\u202f.]', '', raw).replace(',', '.')
    value = float(clean)
    if ',' in raw:
        return '{:,.2f}'.format(value).replace(',', ' ').replace('.', ',')
    return '{:,}'.format(int(value)).replace(',', ' ')


def _parse_date(folded, pos):
    zone = folded[pos:pos + 40]
    for pattern in (DATE_TEXT, DATE_NUM):
        m = pattern.search(zone)
        if not m or m.start() > 8:
            continue
        try:
            if pattern is DATE_TEXT:
                if not m[3]:
                    continue
                return date(int(m[3]), MONTHS[m[2]], int(m[1])), pos + m.end()
            year = int(m[3]) + (2000 if int(m[3]) < 100 else 0)
            return date(year, int(m[2]), int(m[1])), pos + m.end()
        except (ValueError, KeyError):
            continue
    return None, pos


def extract_facts(text, path, modified=''):
    """Faits proposés pour un document : ``[(record, source), ...]``. Chaque valeur figure dans le texte."""
    text = str(text or '')
    quality = text_quality(text)
    folded = fold_chars(text)
    out, seen = [], set()

    def add(kind, title, content, strong, start, end, event_date=''):
        key = (kind, fold(title))
        if key in seen:
            return
        seen.add(key)
        context = _clip(text, start, end)
        sid = 'fact-' + digest('%s|%s|%s' % (path, kind, title))[:20]
        source = {'id': sid, 'kind': 'document', 'path': path, 'modified': modified, 'excerpt': context}
        out.append(({'record_type': kind, 'title': title, 'content': content, 'event_date': event_date,
                     'confidence': _confidence(strong, quality), 'source_ids': [sid], 'actor': ''}, source))

    for m in RG.finditer(text):
        add('case_number', 'RG ' + m[1], 'Numéro de répertoire général : ' + m[1], True, m.start(), m.end())
        break
    for line in text.splitlines()[:60]:
        m = JURISDICTION.search(line)
        if m and len(line.strip()) < 160:
            i = text.find(line)
            add('jurisdiction', m[1].strip(), 'Juridiction : ' + m[1].strip(), True, max(0, i), i + len(line))
            break
    for m in AMOUNT.finditer(text):
        window = fold_chars(text[max(0, m.start() - 110):m.start() + 30])
        if not AMOUNT_CUE.search(window):
            continue
        value = _fr_amount(m[1])
        add('amount', 'Montant : %s €' % value, 'Montant de %s € cité dans la pièce.' % value, True, m.start(), m.end())
        if len([1 for r, _ in out if r['record_type'] == 'amount']) >= 20:
            break
    for label, pattern in EVENT_CUES:
        found = 0
        for m in re.finditer(pattern, folded):   # 5.6.24 (F32) : deux jugements d'une même pièce restent deux faits distincts
            when, end = _parse_date(folded, m.end())
            if not when:
                continue
            title = '%s du %d/%02d/%d' % (label, when.day, when.month, when.year)
            add('event', title, '%s en date du %s.' % (label, when.strftime('%d/%m/%Y')), True, m.start(), end, when.isoformat())
            found += 1
            if found >= 6:
                break
    parties = 0
    for m in PARTY.finditer(text):
        name = party_name(m[1].strip())
        if not name or not looks_like_party(folded, m.start(), m.end(), name):
            continue
        add('party', 'Partie : ' + name, 'Partie désignée dans la pièce : ' + name, False, m.start(), m.end())
        parties += 1
        if parties >= 4:
            break
    for m in PERSON.finditer(text):
        name = party_name(m[2].strip())
        if not name or fold(name.split()[0]) in NOISE_PERSON or parties >= 6 or not looks_like_party(folded, m.start(), m.end(), name):
            continue
        add('party', 'Partie : %s %s' % (m[1], name), 'Personne désignée comme partie dans la pièce : %s %s' % (m[1], name), False, m.start(), m.end())
        parties += 1
    return out, quality


def propose(desk, mid, path, text, modified='', stats=None):
    """Enregistre les faits repérés comme propositions à valider (jamais validés d'office)."""
    ensure_schema(desk)
    facts, quality = extract_facts(text, path, modified)
    created = 0
    for record, source in facts:
        upsert_record(desk, mid, record, [source], 'extraction_deterministe_460', stats=stats)
        created += 1
    desk.db.commit()
    return {'path': path, 'faits': created, 'qualite_texte': quality}


def scan_matter(desk, args):
    """Tâche : propose les faits des pièces indexées du dossier (ou d'une pièce précise)."""
    mid = str(args.get('matter', ''))
    _matter(desk, mid)
    from .index import DocumentIndex
    index = DocumentIndex(desk.c['state_dir'])
    only = str(args.get('path', ''))
    sql, params = 'SELECT path,text,modified FROM docs WHERE matter=? AND error=""', [mid]
    if only:
        sql += ' AND path=?'
        params.append(only)
    # 5.6.24 (F32) : toutes les pièces, par pages de 200 (plus de coupe à quarante) ; compteurs calculés depuis les changements persistés
    sql += ' ORDER BY modified DESC, path LIMIT 200 OFFSET ?'
    total, per_doc, offset, stats = 0, [], 0, {}
    while True:
        rows = index.db.execute(sql, params + [offset]).fetchall()
        if not rows:
            break
        for path, text, modified in rows:
            res = propose(desk, mid, path, text or '', modified or '', stats)
            total += res['faits']
            per_doc.append(res)
        offset += len(rows)
    failed = [r[0] for r in index.db.execute('SELECT path FROM docs WHERE matter=? AND error<>""' + (' AND path=?' if only else ''), [mid] + ([only] if only else []))]
    events = None
    if args.get('sync', True):   # 5.6.24 (F26) : la chronologie est projetée dans le même travail, la commande n'est complète qu'ensuite
        from .legal_memory import sync_timeline
        events = sync_timeline(desk, mid)
    desk.audit('faits_460_proposes', {'matter': mid, 'documents': len(per_doc), 'faits': total, **stats})
    low = [d['path'] for d in per_doc if d['qualite_texte'] < 0.55]
    counts = {k: stats.get(k, 0) for k in ('nouveaux', 'modifies', 'inchanges', 'refus_respectes')}
    return {'status': 'prepared' if total else 'blocked', 'matter': mid, 'documents': len(per_doc), 'faits': total, **counts,
            'erreurs': len(failed), 'documents_en_erreur': failed[:50], 'documents_peu_lisibles': low, 'chronologie': events,
            'traitement': 'repérage déterministe (RG, juridiction, montants, dates d’actes, parties) — pas une analyse sémantique du dossier',
            'message': '%d fait(s) repéré(s) dans %d pièce(s) : %d nouveau(x), %d modifié(s), %d inchangé(s), %d refus respecté(s)%s%s%s.' % (
                total, len(per_doc), counts['nouveaux'], counts['modifies'], counts['inchanges'], counts['refus_respectes'],
                ' ; %d pièce(s) peu lisible(s), confiance basse' % len(low) if low else '', ' ; %d pièce(s) en erreur de lecture' % len(failed) if failed else '',
                ' ; chronologie mise à jour (%d événement(s))' % events if events is not None else '')}


# ------------------------------------------------------------ décisions
def facts_for_review(desk, mid):
    """Faits à afficher, regroupés par état. Les faits refusés restent consultables mais ne sont jamais réutilisés."""
    records = memory_records(desk, mid, None, 300)
    out = []
    for r in records:
        if r['status'] == 'archived':
            continue
        conf = 'high' if r['confidence'] >= 0.85 else 'medium' if r['confidence'] >= 0.6 else 'low'
        out.append({'id': r['id'], 'type': r['record_type'], 'type_label': TYPE_LABELS.get(r['record_type'], r['record_type']),
                    'title': r['title'], 'content': r['content'], 'event_date': r['event_date'], 'status': r['status'],
                    'confidence': conf, 'sources': [{'path': s.get('path', ''), 'excerpt': s.get('excerpt', '')[:300],
                                                    'modified': s.get('modified', '')} for s in r['source_snapshot']],
                    'updated': r['updated']})
    return out


def decide(desk, mid, rid, action, text='', note='', **fields):
    """Valider (éventuellement en corrigeant texte, intitulé, date et acteur) ou refuser, en un clic. 5.6.24 (F27) : ``revision``
    attendue contrôlée ; ``event_date`` confirmée par l'avocat met à jour la date utilisée par la chronologie et la synthèse."""
    row = desk.db.execute('SELECT matter FROM legal_memory_records WHERE id=?', (rid,)).fetchone()
    if not row or row['matter'] != mid:
        raise Stop('information_memoire_absente')
    extra = {k: v for k, v in fields.items() if k in ('title', 'event_date', 'actor', 'revision') and v is not None}
    if action == 'validate':
        return change_record(desk, 'validate_memory', {'record': rid, 'matter': mid, 'text': text, 'note': note or 'Validé par l’avocat', **extra})
    if action == 'reject':
        return change_record(desk, 'dispute_memory', {'record': rid, 'matter': mid, 'note': note or 'Refusé par l’avocat', **({'revision': extra['revision']} if 'revision' in extra else {})})
    raise Stop('action_memoire_invalide')


def reusable_facts(desk, mid):
    """Seuls les faits validés ou épinglés sont réutilisables dans les rédactions."""
    return [r for r in memory_records(desk, mid, ['validated', 'pinned'], 200)]
