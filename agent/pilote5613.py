"""5.6.13 : un seul Pilote, des résultats explicites.

- Intention d'une instruction (Question / Analyse / Document / Brouillon de courriel) annoncée avant de démarrer, jamais déduite d'un
  seul mot comme « contrat » ou « audience ».
- Rédaction libre convergente : les projets Word reprennent le modèle du cabinet (en-têtes, pieds de page, styles) quand il existe,
  une révision conserve le document d'origine, et chaque projet passe par le contrôle juridique (mentions, citations, second modèle).
- Trois états distincts : « Dépôt vérifié », « Contrôle juridique effectué », « Validation de l'avocat ».
- Manifeste des sources (conclusions retenues, pièces, extraits, courriels, non-lus), comparaison des versions lors d'une révision,
  proposition de règle réversible après une correction.
"""
from datetime import datetime
from html import escape
import json
import re
import zipfile
import io
from pathlib import PurePosixPath

from .common import Stop, digest, fold, load_matters, matter_display

INTENTS = ('auto', 'question', 'analysis', 'document', 'mail', 'mission', 'facturation')
DELIVERABLES = {'question': 'Réponse dans le fil', 'analysis': 'Analyse dans le fil', 'document': 'Word dans ce dossier',
                'mail': 'Brouillon dans Drafts', 'mission': 'Mission complexe (parcours par tâches)', 'facturation': 'Facturation Invoice Ninja (aperçu puis validation)'}
QUESTION_STARTS = ('quel ', 'quels ', 'quelle ', 'quelles ', 'est-ce ', 'est ce ', 'comment ', 'pourquoi ', 'combien ', 'que ', 'qu ',
                   'qui ', 'ou ', 'quand ', 'peut-on ', 'peut on ', 'dois-je ', 'dois je ', 'faut-il ', 'faut il ', 'y a-t-il ', 'a-t-on ',
                   'existe-t-il ', 'sommes-nous ', 'suis-je ', 'puis-je ')
ANALYSIS_VERBS = ('analyse', 'analyser', 'resume', 'resumer', 'explique', 'expliquer', 'compare', 'comparer', 'verifie', 'verifier',
                  'identifie', 'identifier', 'liste', 'lister', 'synthetise', 'synthetiser', 'relis', 'relire', 'examine', 'examiner',
                  'evalue', 'evaluer', 'releve', 'relever', 'decris', 'decrire', 'recense', 'recenser', 'indique', 'indiquer')
NO_PRODUCTION = ('sans rediger', 'sans redaction', 'sans document', 'sans creer', 'sans produire', 'ne redige pas', 'pas de document',
                 'pas de word', 'dans le fil', 'sans projet', 'sans fichier', 'oralement', 'en quelques lignes')
MAIL_WORDS = r'\b(courriel|courriels|mail|mails|e-mail|email|emails|message electronique)\b'
MAIL_VERBS = r'\b(prepare|preparer|redige|rediger|ecris|ecrire|reponds|repondre|repond|reponse|envoie|envoyer|brouillon)\b'
PRODUCTION_WORDS = ('plaidoirie', 'audience', 'conclusions', 'contrat', 'assignation', 'requete', 'note', 'consultation', 'courrier',
                    'lettre', 'mise en demeure', 'compte rendu', 'protocole', 'avenant')


# ---------------------------------------------------------------------------------------- intention
def classify_intent(text, mail_key=''):
    """Intention d'une instruction : question, analyse (sans production), document Word ou brouillon de courriel.

    Une interrogation ou une demande d'analyse ne crée jamais de fichier, même si elle cite un « contrat » ou des « conclusions »."""
    from .cockpit530 import DOC_VERBS, is_document_request
    f = ' ' + re.sub(r'\s+', ' ', fold(text)).strip() + ' '
    body = f.strip()
    first = body.split(' ', 1)[0] if body else ''
    doc_verb = any(' %s ' % v in f for v in DOC_VERBS)
    reasons = []
    if any(' ' + w + ' ' in f or f.rstrip().endswith(' ' + w) for w in NO_PRODUCTION):
        return {'intent': 'analysis', 'reasons': ['La demande exclut toute production (« sans rédiger », « dans le fil »…).']}
    # 5.6.14 : parcours complexes (assignation, conclusions) et facturation, proposés puis confirmés avant lancement
    from .parcours5614 import detect
    if detect(text) and (doc_verb or first in ('reponds', 'repondre', 'repond', 'replique')):
        return {'intent': 'mission', 'reasons': ['Parcours « %s » proposé : mission complexe par tâches (cadrage, lectures, qualification, recherche, rédaction, contrôle).' % detect(text)]}
    if re.search(r'\b(devis|facture|invoice ninja|projet invoice|client factur)', f) and doc_verb:
        return {'intent': 'facturation', 'reasons': ['Parcours de facturation : aperçu client, projet et prestations, puis validation avant toute écriture Invoice Ninja.']}
    if re.search(MAIL_WORDS, f) and (re.search(MAIL_VERBS, f) or doc_verb):
        reasons.append('La demande vise un courriel' + (' en réponse au message sélectionné.' if mail_key else ' : un brouillon sera déposé dans Drafts, sans envoi.'))
        return {'intent': 'mail', 'reasons': reasons}
    if first in ('reponds', 'repondre', 'repond') and not body.endswith('?'):
        return {'intent': 'mail', 'reasons': ['Demande de réponse : brouillon de courriel' + (' au message sélectionné.' if mail_key else ' dans Drafts, destinataire à renseigner.')]}
    if body.endswith('?') or any(f.lstrip().startswith(s) for s in QUESTION_STARTS):
        return {'intent': 'question', 'reasons': ['Formulation interrogative : réponse dans le fil, aucun document créé.']}
    if first in ANALYSIS_VERBS or any(f.lstrip().startswith(x) for x in ('fais une analyse', 'fais un resume', 'fais le point', 'donne-moi', 'donne moi', 'dis-moi', 'dis moi')):
        return {'intent': 'analysis', 'reasons': ['Verbe d’analyse en tête de demande : analyse dans le fil, aucun document créé.']}
    if is_document_request(text) or (doc_verb and any(' ' + w + ' ' in f for w in PRODUCTION_WORDS)):
        return {'intent': 'document', 'reasons': ['Verbe de production et type de document reconnus : projet Word dans le dossier.']}
    if mail_key and re.search(MAIL_VERBS, f):
        return {'intent': 'mail', 'reasons': ['Un courriel est sélectionné et la demande évoque une réponse.']}
    return {'intent': 'question', 'reasons': ['Aucun verbe de production reconnu : réponse dans le fil.']}


def resolve_intent(text, requested='auto', mail_key=''):
    requested = str(requested or 'auto')
    if requested not in INTENTS:
        raise Stop('intention_invalide')
    guess = classify_intent(text, mail_key)
    if requested != 'auto':
        return {'intent': requested, 'label': DELIVERABLES[requested], 'reasons': ['Résultat choisi explicitement.'],
                'guessed': guess['intent'], 'explicit': True}
    return {'intent': guess['intent'], 'label': DELIVERABLES[guess['intent']], 'reasons': guess['reasons'], 'guessed': guess['intent'], 'explicit': False}


def preview(desk, data, owner='cabinet'):
    """Ce que produirait la mission, avant de la démarrer : résultat attendu, dossier, couverture des sources."""
    text = str(data.get('instruction') or '').strip()
    if not 3 <= len(text) <= 12000:
        raise Stop('question_requise_12000_caracteres_maximum')
    ctx = data.get('context') or {}
    if not isinstance(ctx, dict):
        ctx = {}
    mail_key = str(ctx.get('mail_key') or '')
    out = resolve_intent(text, data.get('intent') or 'auto', mail_key)
    from .docrequest520 import find_matter
    matters = {str(m['id']): m for m in load_matters(desk.c)}
    chosen = str(data.get('matter') or '')
    named, candidates = find_matter(desk, text)
    matter = chosen if chosen in matters else (named['id'] if named else '')
    out['matter'] = matter
    out['matter_label'] = matter_display(matters[matter]) if matter in matters else ''
    out['ambiguous'] = not matter and bool(candidates)
    out['candidates'] = [{'id': m['id'], 'label': matter_display(m)} for m in candidates[:6]] if not matter else []
    attachments = [str(a) for a in (data.get('attachments') or []) if a][:3]
    selected = [str(p) for p in (ctx.get('selected_documents') or [])][:20]
    out['sources'] = {'selected_documents': len(selected), 'attachments': len(attachments), 'mail': bool(mail_key),
                      'matter_sources': bool(matter)}
    out['needs_matter'] = out['intent'] in ('document', 'mission', 'facturation') and not matter
    if out['intent'] == 'mission':
        from .parcours5614 import detect
        out['parcours'] = detect(text) or 'assignation'
    if out['intent'] == 'facturation' and matter:
        try:
            from .facturation5614 import missing_for
            out['facturation'] = missing_for(desk, matter, 'quote')
        except Stop as ex:
            out['facturation'] = {'missing': [str(ex)], 'ready': False}
    out['options'] = [{'intent': k, 'label': v} for k, v in DELIVERABLES.items()]
    out['summary'] = out['label'] + (' · ' + out['matter_label'] if out['matter_label'] else ' · dossier à préciser' if out['needs_matter'] or out['ambiguous'] else ' · tout le cabinet')
    return out


# ---------------------------------------------------------------------------------------- modèles du cabinet
TEMPLATE_WORDS = {'courrier': ('courrier', 'lettre', 'papier'), 'conclusions': ('conclusions',), 'assignation': ('assignation', 'requete'),
                  'mise_en_demeure': ('mise en demeure', 'demeure'), 'contrat': ('contrat', 'acte'), 'note': ('note', 'consultation', 'memo'),
                  'compte_rendu': ('compte rendu', 'compte-rendu'), 'courriel': (), 'autre': (), 'auto': ()}
GENERIC_WORDS = ('generique', 'en-tete', 'entete', 'papier', 'standard')   # 5.6.14 (C10) : « cabinet » n'est plus un repli générique
FIELD_LABELS = {'destinataire': 'destinataire', 'qualite': 'qualité', 'adresse': 'adresse', 'reference': 'référence', 'objet': 'objet', 'date': 'date',
                'envoi': 'mode d’envoi', 'appel': 'formule d’appel', 'corps': 'corps', 'fin': 'formule finale', 'signature': 'signature'}


def template_for_kind(desk, kind):
    """(ligne du modèle approuvé, octets) pour ce type d'écrit ; (None, None) sans modèle adapté.

    Choix explicite de l'avocat (réglage docreq5613:templates), sinon libellé du modèle contenant le type, sinon modèle générique."""
    from . import cabinet_docs33
    try:
        rows = [r for r in cabinet_docs33.templates(desk) if r.get('status') == 'approved']
    except Exception:
        return None, None
    if not rows:
        return None, None
    chosen = (desk.settings('docreq5613:templates', {}) or {}).get(kind, '')
    pick = next((r for r in rows if r['id'] == chosen), None)
    if pick is None:
        words = TEMPLATE_WORDS.get(kind, ())
        pick = next((r for r in rows if any(fold(w) in fold(r['label']) for w in words)), None) if words else None
    if pick is None:
        pick = next((r for r in rows if any(w in fold(r['label']) for w in GENERIC_WORDS)), None)
    if pick is None:
        return None, None
    path = cabinet_docs33._root(desk) / 'templates' / (pick['id'] + '.docx')
    try:
        raw = path.read_bytes()
    except OSError:
        return None, None
    if cabinet_docs33._sha(raw) != pick['sha256']:
        return None, None
    return pick, raw


def _lines(doc, ctx, request, internal_notes=False):
    """Paragraphes du projet, à plat, pour le corps d'un modèle (titres en capitales, listes à tiret).
    5.6.14 (U06) : les notes internes (sources, points à compléter, mention AxiorHub) ne vont dans le corps que sur demande ; la fiche de
    contrôle les porte."""
    lines = []
    if doc.get('titre') and not (doc['paragraphes'] and doc['paragraphes'][0].get('style') == 'titre'):
        lines.append(str(doc['titre']).upper())
    for p in doc['paragraphes']:
        for line in str(p['texte']).split('\n'):
            if not line.strip():
                continue
            style = p.get('style', 'texte')
            lines.append(line.strip().upper() if style in ('titre', 'intertitre') else ('– ' + line.strip().lstrip('-–• ')) if style == 'liste' else line.strip())
    if not internal_notes:
        return lines
    lines.append('')
    lines.append('Projet préparé par AxiorHub Pilote le %s — à relire et compléter par l’avocat.' % datetime.now().strftime('%d/%m/%Y'))
    if doc.get('a_completer'):
        lines.append('Points à compléter : ' + ' ; '.join(doc['a_completer']))
    used = set(doc.get('sources', []))
    refs = [x for x in ctx.get('extraits', []) + ctx.get('pieces_jointes', []) + ctx.get('courriels', []) if x.get('source') in used]
    if refs:
        lines.append('Sources utilisées : ' + ' ; '.join('%s — %s' % (r['source'], r.get('fichier') or ('courriel de %s du %s' % (r.get('de', ''), r.get('date', '')))) for r in refs))
    return lines


def build_from_template_ex(desk, raw, doc, ctx, request, matter, internal_notes=False, fields=None):
    """Projet Word construit dans le modèle du cabinet : en-têtes, pieds de page, styles et balises conservés.
    5.6.14 (C10) : chaque champ du modèle a une provenance ; une valeur manquante reste un marqueur visible et un champ à compléter."""
    from . import cabinet_docs33
    info = cabinet_docs33.inspect_template(raw)
    today = datetime.now().strftime('%d/%m/%Y')
    known = {'reference': matter_display(matter), 'objet': str(doc.get('titre') or request[:120]), 'date': today, 'corps': '\n'.join(_lines(doc, ctx, request, internal_notes)),
             'appel': 'Madame, Monsieur,', 'fin': 'Je vous prie d’agréer, Madame, Monsieur, l’expression de mes salutations distinguées.', 'envoi': '', 'signature': ''}
    known.update({k: str(v) for k, v in (fields or {}).items() if v})
    values, missing, provenance = {}, [], {}
    for name in info['placeholders']:
        if name in known and (known[name] or name in ('envoi', 'signature')):
            values[name] = known[name]
            provenance[name] = 'champ fourni' if name in (fields or {}) else ('dossier' if name == 'reference' else 'projet' if name in ('objet', 'corps') else 'valeur par défaut')
        else:
            values[name] = '[À COMPLÉTER : %s]' % FIELD_LABELS.get(name, name)
            missing.append(name)
            provenance[name] = 'manquant'
    return cabinet_docs33.fill_template(raw, values), {'missing_fields': missing, 'provenance': provenance, 'placeholders': info['placeholders']}


def build_from_template(desk, raw, doc, ctx, request, matter, internal_notes=False, fields=None):
    return build_from_template_ex(desk, raw, doc, ctx, request, matter, internal_notes, fields)[0]


def _split_body(inner):
    """Éléments de premier niveau du corps (paragraphes, tableaux, contrôles de contenu, autres), dans l'ordre."""
    out, pos, n = [], 0, len(inner)
    tag = re.compile(r'<(/?)w:(p|tbl|sdt)\b[^>]*?(/?)>')
    while pos < n:
        m = tag.search(inner, pos)
        if not m:
            if inner[pos:].strip():
                out.append(('other', inner[pos:]))
            break
        if m.start() > pos and inner[pos:m.start()].strip():
            out.append(('other', inner[pos:m.start()]))
        if m.group(1) == '/':
            out.append(('other', m.group(0)))
            pos = m.end()
            continue
        kind = m.group(2)
        if m.group(3) == '/':
            out.append((kind, m.group(0)))
            pos = m.end()
            continue
        depth, cur = 1, m.end()
        same = re.compile(r'<(/?)w:%s\b[^>]*?(/?)>' % kind)
        while depth and cur < n:
            m2 = same.search(inner, cur)
            if not m2:
                cur = n
                break
            cur = m2.end()
            if m2.group(2) == '/':
                continue
            depth += -1 if m2.group(1) == '/' else 1
        out.append((kind, inner[m.start():cur]))
        pos = cur
    return out


def _ptext(xml):
    return re.sub(r'\s+', ' ', ''.join(re.findall(r'<w:t(?:\s[^>]*)?>(.*?)</w:t>', xml, flags=re.S))).strip()


def revise_body(previous_raw, body_xml):
    """5.6.14 (C03) : révision par blocs. Les paragraphes inchangés gardent leur XML (styles, numérotation, notes), les tableaux, images
    et contrôles de contenu restent à leur place ; seuls les paragraphes modifiés, ajoutés ou retirés changent. Retourne (octets, rapport)."""
    import difflib
    if not previous_raw.startswith(b'PK'):
        raise Stop('document_precedent_non_word')
    source, out = io.BytesIO(previous_raw), io.BytesIO()
    with zipfile.ZipFile(source) as zin, zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as zout:
        if 'word/document.xml' not in zin.namelist():
            raise Stop('document_precedent_non_word')
        xml = zin.read('word/document.xml').decode('utf-8', 'replace')
        start = xml.find('<w:body>')
        if start < 0:
            start = xml.find('<w:body ')
            if start < 0:
                raise Stop('document_precedent_non_word')
            start = xml.find('>', start) + 1
        else:
            start += len('<w:body>')
        end = xml.rfind('</w:body>')
        sect = xml.rfind('<w:sectPr', start, end)
        tail = xml[sect:end] if sect > start else ''
        inner = xml[start:sect if sect > start else end]
        old_elems = _split_body(inner)
        old_p = [(i, _ptext(x)) for i, (k, x) in enumerate(old_elems) if k == 'p']
        new_p = [x for k, x in _split_body(body_xml) if k == 'p']
        new_t = [_ptext(x) for x in new_p]
        sm = difflib.SequenceMatcher(None, [t for _, t in old_p], new_t, autojunk=False)
        attached = {}
        count = 0
        for i, (k, x) in enumerate(old_elems):
            if k == 'p':
                count += 1
            else:
                attached.setdefault(count, []).append(x)
        pieces = list(attached.get(0, []))
        report = {'kept': 0, 'replaced': 0, 'inserted': 0, 'deleted': 0, 'tables_kept': sum(1 for k, _ in old_elems if k == 'tbl'),
                  'other_kept': sum(1 for k, _ in old_elems if k not in ('p', 'tbl'))}
        def emit_old(j):
            pieces.extend(attached.get(j + 1, []))
        for op, i1, i2, j1, j2 in sm.get_opcodes():
            if op == 'equal':
                for off in range(i2 - i1):
                    pieces.append(old_elems[old_p[i1 + off][0]][1])
                    emit_old(i1 + off)
                report['kept'] += i2 - i1
            elif op == 'replace':
                pieces.extend(new_p[j1:j2])
                for off in range(i2 - i1):
                    emit_old(i1 + off)
                report['replaced'] += i2 - i1
                report['inserted'] += max(0, (j2 - j1) - (i2 - i1))
            elif op == 'delete':
                for off in range(i2 - i1):
                    emit_old(i1 + off)
                report['deleted'] += i2 - i1
            elif op == 'insert':
                pieces.extend(new_p[j1:j2])
                report['inserted'] += j2 - j1
        document = xml[:start] + ''.join(pieces) + tail + xml[end:]
        for info in zin.infolist():
            if info.filename == 'word/document.xml' or info.filename.endswith('vbaProject.bin'):
                continue
            zout.writestr(info, zin.read(info.filename))
        zout.writestr('word/document.xml', document.encode('utf-8'))
    report['summary'] = '%d paragraphe(s) conservé(s), %d remplacé(s), %d ajouté(s), %d retiré(s) ; %d tableau(x) conservé(s)' % (
        report['kept'], report['replaced'], report['inserted'], report['deleted'], report['tables_kept'])
    return out.getvalue(), report


def replace_body(previous_raw, body_xml):
    """Réécriture complète du corps (mode explicite) : même paquet, corps intégralement remplacé."""
    if not previous_raw.startswith(b'PK'):
        raise Stop('document_precedent_non_word')
    source, out = io.BytesIO(previous_raw), io.BytesIO()
    with zipfile.ZipFile(source) as zin, zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as zout:
        if 'word/document.xml' not in zin.namelist():
            raise Stop('document_precedent_non_word')
        xml = zin.read('word/document.xml').decode('utf-8', 'replace')
        start = xml.find('<w:body>')
        if start < 0:
            start = xml.find('<w:body ')
            if start < 0:
                raise Stop('document_precedent_non_word')
            start = xml.find('>', start) + 1
        else:
            start += len('<w:body>')
        sect = xml.rfind('<w:sectPr')
        end = xml.rfind('</w:body>')
        if end < 0:
            raise Stop('document_precedent_non_word')
        tail = xml[sect:end] if sect > start else ''
        document = xml[:start] + body_xml + tail + xml[end:]
        for info in zin.infolist():
            if info.filename == 'word/document.xml' or info.filename.endswith('vbaProject.bin'):
                continue
            zout.writestr(info, zin.read(info.filename))
        zout.writestr('word/document.xml', document.encode('utf-8'))
    return out.getvalue()


# ---------------------------------------------------------------------------------------- contrôle juridique
LEGAL_KINDS = {'conclusions': 'conclusions', 'assignation': 'assignation', 'mise_en_demeure': 'mise_en_demeure'}


def plain_text(doc):
    return '\n'.join([str(doc.get('titre') or '')] + [str(p.get('texte', '')) for p in doc.get('paragraphes', [])]).strip()


def _control_model(desk):
    from .model import Model, routed_config
    return Model(routed_config(desk.c, 'control'))


def control_document(desk, kind, text, matter_id):
    """Contrôle juridique d'un projet libre : mentions obligatoires et citations (contrôle local), puis relecture par un second modèle
    distinct du rédacteur. Le résultat dit explicitement ce qui a été vérifié et ce qui ne l'a pas été."""
    out = {'done': False, 'execution': 'non_executee', 'outcome': 'indisponible', 'legal': None, 'review': None, 'skipped': '', 'summary': '', 'coverage': {}}
    try:
        from .control470 import quality
        q = quality(desk, matter_id, LEGAL_KINDS.get(kind, ''), text)
        mentions = q.get('mentions')
        out['legal'] = {'mentions_ok': (bool(mentions['ok']) if mentions else None),
                        'missing': [x['label'] for x in (mentions or {}).get('missing', [])][:12],
                        'citations_headline': str((q.get('citations') or {}).get('headline', ''))[:300],
                        'citations_needs_check': bool((q.get('citations') or {}).get('needs_check'))}
    except Exception as ex:   # le contrôle local ne doit jamais empêcher le dépôt du projet
        out['legal'] = {'error': str(ex)[:120]}
    from . import economie569
    if not desk.settings('automation:second_model_control_enabled', True):
        out['skipped'] = 'controle_second_modele_desactive'
    elif not economie569.control_required(desk, 'docrequest520'):
        out['skipped'] = 'regime_econome'
    else:
        try:
            from .model import routed_config
            primary, control = routed_config(desk.c, 'document_drafting'), routed_config(desk.c, 'control')
            if (primary.get('provider_id'), primary.get('model')) == (control.get('provider_id'), control.get('model')):
                out['review'] = {'status': 'not_independent', 'recommendation': 'unavailable',
                                 'risks': ['Le modèle de contrôle est identique au modèle rédacteur : configurer un second modèle distinct.']}
            else:
                review = _control_model(desk).ask('second_model_review', {
                    'job_kind': 'docrequest520', 'primary_provider': primary.get('provider_id', 'ollama'), 'primary_model': primary.get('model', ''),
                    'result': json.dumps({'kind': kind, 'text': text[:24000]}, ensure_ascii=False),
                    'limits': 'Contrôle interne seulement. Ne pas exécuter, envoyer, déposer ou modifier.'})
                if not isinstance(review, dict):
                    raise Stop('controle_invalide')
                review['status'] = 'completed'
                out['review'] = {k: review.get(k) for k in ('status', 'recommendation', 'agree', 'unsupported_claims', 'omissions', 'risks') if k in review}
                out['done'] = True
                if len(text) > 24000:
                    # 5.6.14 (C05) : le texte entier est relu par blocs par controle5614 ; ici seule la relecture d'ensemble est faite
                    out['coverage'] = {'chars_reviewed': 24000, 'chars_total': len(text), 'complete': False}
                else:
                    out['coverage'] = {'chars_reviewed': len(text), 'chars_total': len(text), 'complete': True}
        except (Stop, OSError, ValueError, KeyError, TypeError) as ex:
            out['review'] = {'status': 'unavailable', 'recommendation': 'unavailable', 'error': str(ex)[:120]}
    legal = out['legal'] or {}
    # 5.6.14 (C04) : exécution ≠ résultat
    review = out['review'] or {}
    out['execution'] = 'executee' if out['done'] and out['coverage'].get('complete', True) else ('partielle' if (out['done'] or legal) else 'non_executee')
    blocking = legal.get('mentions_ok') is False or str(review.get('recommendation', '')).lower() in ('block', 'reject', 'bloquer')
    reserves = (legal.get('citations_needs_check') or review.get('agree') is False or str(review.get('recommendation', '')).lower() in ('revise', 'reviser')
                or bool(review.get('unsupported_claims')) or bool(review.get('omissions')) or not out['coverage'].get('complete', True))
    if blocking:
        out['outcome'] = 'bloque'
    elif not out['done']:
        out['outcome'] = 'indisponible' if not legal else 'reserves'
    elif reserves:
        out['outcome'] = 'reserves'
    else:
        out['outcome'] = 'reussi'
    parts = [{'reussi': 'contrôle réussi', 'reserves': 'contrôle avec réserves', 'bloque': 'contrôle bloquant', 'indisponible': 'contrôle indisponible'}[out['outcome']]]
    if legal.get('mentions_ok') is False:
        parts.append('mentions manquantes : ' + '; '.join(legal.get('missing', [])))
    elif legal.get('mentions_ok') is True:
        parts.append('mentions obligatoires présentes')
    if legal.get('citations_needs_check'):
        parts.append('références juridiques à vérifier')
    if out['done']:
        parts.append('relecture par un second modèle : ' + str((out['review'] or {}).get('recommendation', '')))
    elif out['skipped']:
        parts.append({'regime_econome': 'second modèle non sollicité (régime économe)', 'controle_second_modele_desactive': 'second modèle désactivé'}[out['skipped']])
    else:
        parts.append('second modèle indisponible : ' + str((out['review'] or {}).get('error') or (out['review'] or {}).get('risks', [''])[0] or ''))
    out['summary'] = ' · '.join(parts)
    return out


# ---------------------------------------------------------------------------------------- manifeste, versions, règles
def manifest(ctx):
    """Ce que la rédaction a réellement reçu : conclusions retenues (version = date de modification), pièces, extraits, courriels,
    et ce qui n'a pas été lu."""
    files = ctx.get('fichiers', []) or []
    conclusions = [x for x in files if 'conclusion' in fold(x.get('fichier', ''))][:6]
    extracts = sorted({x['fichier'] for x in ctx.get('extraits', []) if x.get('fichier')})
    pieces = [p.get('fichier', '') for p in ctx.get('pieces_jointes', [])]
    read = {fold(PurePosixPath(str(f)).name) for f in extracts + pieces}
    unread = [x['fichier'] for x in files if fold(PurePosixPath(str(x['fichier'])).name) not in read]
    return {'conclusions_retenues': [{'fichier': x['fichier'], 'version': x.get('modifie', '')} for x in conclusions],
            'pieces_selectionnees': pieces, 'extraits_utilises': extracts, 'courriels': len(ctx.get('courriels', [])),
            'faits': len(ctx.get('faits', [])), 'agenda': len(ctx.get('agenda', [])), 'fichiers_total': len(files),
            'non_lus': unread[:40], 'non_lus_total': len(unread)}


AMOUNT = re.compile(r'\d[\d  .]*(?:,\d+)?\s?(?:€|euros?)\b', re.I)
DATE = re.compile(r'\b\d{1,2}/\d{1,2}/\d{2,4}\b|\b\d{1,2}(?:er)?\s+(?:janvier|février|fevrier|mars|avril|mai|juin|juillet|août|aout|septembre|octobre|novembre|décembre|decembre)\s+\d{4}\b', re.I)
DEMAND = re.compile(r'^(?:condamner|declarer|dire|juger|ordonner|debouter|prononcer|fixer|constater|enjoindre|designer)\b', re.I)


def _paragraphs(text):
    return [re.sub(r'\s+', ' ', x).strip() for x in re.split(r'\n\s*\n|\n', str(text or '')) if x.strip()]


def _section(text, marker):
    f = fold(text)
    at = f.find(marker)
    return f[at:] if at >= 0 else ''


def revision_diff(previous, current):
    """5.6.14 (C07) : comparaison ordonnée par blocs — ajout, retrait, modification, déplacement, doublon supprimé — avec montants
    (€, euros, décimales, espaces insécables) et dates (françaises et ISO) normalisés, localisation avant/après."""
    import difflib
    from collections import Counter
    from .controle5614 import amounts as _amounts, dates as _dates
    before, after = _paragraphs(previous), _paragraphs(current)
    sb, sa = set(before), set(after)
    amounts_before = {v for v, _, _ in _amounts(previous or '')}
    amounts_after = {v for v, _, _ in _amounts(current or '')}
    dates_before = {v for v, _, _ in _dates(previous or '')}
    dates_after = {v for v, _, _ in _dates(current or '')}
    changes = []
    sm = difflib.SequenceMatcher(None, before, after, autojunk=False)
    cb, ca = Counter(before), Counter(after)
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == 'equal':
            continue
        if op == 'replace':
            for k in range(max(i2 - i1, j2 - j1)):
                old = before[i1 + k] if i1 + k < i2 else ''
                new = after[j1 + k] if j1 + k < j2 else ''
                if old and new and difflib.SequenceMatcher(None, old, new).ratio() > 0.6:
                    changes.append({'kind': 'modification', 'avant': old, 'apres': new, 'position': j1 + k + 1})
                elif old and new:
                    changes.append({'kind': 'retrait' if old in ca else 'retrait', 'avant': old, 'apres': '', 'position': i1 + k + 1})
                    changes.append({'kind': 'ajout', 'avant': '', 'apres': new, 'position': j1 + k + 1})
                elif old:
                    changes.append({'kind': 'retrait', 'avant': old, 'apres': '', 'position': i1 + k + 1})
                else:
                    changes.append({'kind': 'ajout', 'avant': '', 'apres': new, 'position': j1 + k + 1})
        elif op == 'delete':
            for k in range(i1, i2):
                changes.append({'kind': 'retrait', 'avant': before[k], 'apres': '', 'position': k + 1})
        elif op == 'insert':
            for k in range(j1, j2):
                changes.append({'kind': 'ajout', 'avant': '', 'apres': after[k], 'position': k + 1})
    # déplacements : retiré ici et ajouté ailleurs ; doublons supprimés : présent deux fois avant, une fois après
    removed = {c['avant'] for c in changes if c['kind'] == 'retrait'}
    added = {c['apres'] for c in changes if c['kind'] == 'ajout'}
    for c in changes:
        if c['kind'] == 'retrait' and c['avant'] in added:
            c['kind'] = 'deplacement'
        elif c['kind'] == 'ajout' and c['apres'] in removed:
            c['kind'] = 'deplacement'
        elif c['kind'] == 'retrait' and cb[c['avant']] > ca.get(c['avant'], 0) >= 1:
            c['kind'] = 'doublon_supprime'
    demands_before = {fold(p) for p in before if DEMAND.match(fold(p))}
    demands_after = {fold(p) for p in after if DEMAND.match(fold(p))}
    disp_before, disp_after = _section(previous, 'par ces motifs'), _section(current, 'par ces motifs')
    out = {'changes': changes[:120], 'added': [c['apres'] for c in changes if c['kind'] == 'ajout'][:40], 'removed': [c['avant'] for c in changes if c['kind'] == 'retrait'][:40],
           'added_count': sum(1 for c in changes if c['kind'] == 'ajout'), 'removed_count': sum(1 for c in changes if c['kind'] == 'retrait'),
           'modified_count': sum(1 for c in changes if c['kind'] == 'modification'), 'moved_count': sum(1 for c in changes if c['kind'] == 'deplacement') // 2,
           'duplicates_removed': sum(1 for c in changes if c['kind'] == 'doublon_supprime'),
           'amounts': {'added': sorted(amounts_after - amounts_before)[:20], 'removed': sorted(amounts_before - amounts_after)[:20]},
           'dates': {'added': sorted(dates_after - dates_before)[:20], 'removed': sorted(dates_before - dates_after)[:20]},
           'demands_changed': demands_before != demands_after,
           'dispositif_changed': bool(disp_before or disp_after) and disp_before != disp_after}
    alerts = []
    if out['amounts']['added'] or out['amounts']['removed']:
        alerts.append('montant(s) modifié(s)')
    if out['dates']['added'] or out['dates']['removed']:
        alerts.append('date(s) modifiée(s)')
    if out['demands_changed']:
        alerts.append('demande(s) modifiée(s)')
    if out['dispositif_changed']:
        alerts.append('dispositif modifié')
    out['alerts'] = alerts
    out['summary'] = '%d ajouté(s), %d retiré(s), %d modifié(s), %d déplacé(s), %d doublon(s) supprimé(s)' % (
        out['added_count'], out['removed_count'], out['modified_count'], out['moved_count'], out['duplicates_removed']) + (' · ' + ', '.join(alerts) if alerts else ' · ni montant, ni date, ni demande, ni dispositif modifiés')
    return out


RULE_SCHEMA = '''CREATE TABLE IF NOT EXISTS rule_proposals5613(
 id TEXT PRIMARY KEY, matter TEXT NOT NULL, kind TEXT NOT NULL, purpose TEXT NOT NULL, rule_type TEXT NOT NULL,
 instruction TEXT NOT NULL, source_request TEXT NOT NULL, state TEXT NOT NULL, rule_id INTEGER, created TEXT NOT NULL, updated TEXT NOT NULL);'''
RULE_WORDS = (('style', ('formule', 'tutoie', 'vouvoie', 'ton ', 'style', 'signature', 'politesse', 'courtois', 'ferme', 'sobre', 'long', 'court', 'bref', 'develop', 'concis', 'resum')),
              ('structure', ('plan', 'structure', 'partie', 'titre', 'paragraphe', 'ordre', 'section', 'intertitre', 'numerot', 'dispositif', 'moyen')),
              ('word_template', ('modele', 'en-tete', 'entete', 'papier', 'mise en forme', 'police')),
              ('legal_position', ('fondement', 'article', 'jurisprudence', 'position', 'argument', 'demande', 'pretention')))


def _rule_schema(desk):
    desk.db.executescript(RULE_SCHEMA)


def propose_rule(desk, kind, instruction, matter_id, request_id):
    """Après une correction, une règle précise et réversible est proposée (jamais appliquée d'office)."""
    from .docrequest520 import KINDS
    _rule_schema(desk)
    text = re.sub(r'\s+', ' ', str(instruction or '')).strip()
    if len(text) < 6:
        return None
    f = fold(text)
    rule_type = next((t for t, words in RULE_WORDS if any(w in f for w in words)), 'other')
    label = KINDS.get(kind, kind) if kind not in ('auto', 'autre', '') else 'documents'
    proposal = 'Pour les %s : %s' % (label.lower(), text[:600])
    pid = digest('rule5613|' + proposal + '|' + str(matter_id))[:32]
    desk.db.execute('INSERT OR IGNORE INTO rule_proposals5613 VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                    (pid, str(matter_id or ''), kind, 'document_drafting', rule_type, proposal, str(request_id), 'proposed', None, desk.now(), desk.now()))
    desk.db.commit()
    return {'id': pid, 'rule_type': rule_type, 'instruction': proposal, 'state': 'proposed'}


def rule_proposals(desk, state='proposed', limit=20):
    _rule_schema(desk)
    return [dict(r) for r in desk.db.execute('SELECT * FROM rule_proposals5613 WHERE state=? ORDER BY created DESC LIMIT ?', (state, limit))]


def rule_proposal(desk, pid):
    _rule_schema(desk)
    row = desk.db.execute('SELECT * FROM rule_proposals5613 WHERE id=?', (str(pid),)).fetchone()
    return dict(row) if row else None


def decide_rule(desk, pid, action):
    """adopt : règle du cabinet (réversible dans Règles métier) ; adopt_matter : limitée au dossier ; ignore : écartée."""
    row = rule_proposal(desk, pid)
    if not row:
        raise Stop('proposition_regle_absente')
    if row['state'] != 'proposed':
        raise Stop('proposition_regle_deja_traitee')
    if action not in ('adopt', 'adopt_matter', 'ignore'):
        raise Stop('action_regle_invalide')
    rule_id = None
    if action != 'ignore':
        from .learning410 import save_rule
        scope, value = ('matter', row['matter']) if action == 'adopt_matter' and row['matter'] else ('cabinet', '')
        rule_id = save_rule(desk, scope, value, row['purpose'], row['rule_type'], row['instruction'], 'correction', row['source_request'])['id']
    desk.db.execute('UPDATE rule_proposals5613 SET state=?,rule_id=?,updated=? WHERE id=?',
                    ('ignored' if action == 'ignore' else 'adopted', rule_id, desk.now(), pid))
    desk.db.commit()
    desk.audit('regle5613_decision', {'proposal': pid, 'action': action, 'rule_id': rule_id})
    return {'id': pid, 'state': 'ignored' if action == 'ignore' else 'adopted', 'rule_id': rule_id,
            'message': 'Règle écartée.' if action == 'ignore' else 'Règle enregistrée (modifiable ou suspendue dans Règles métier).'}


# ---------------------------------------------------------------------------------------- dossiers et pièces
def matters_listing(desk):
    """Dossiers pour la sélection : nom complet, termes de recherche (client, adversaire, référence, alias), récents en premier."""
    matters = load_matters(desk.c)
    recent = {}
    for sql in ("SELECT matter,MAX(updated) FROM missions_v567 GROUP BY matter",
                "SELECT matter,MAX(updated) FROM docreq520 GROUP BY matter",
                "SELECT matter,MAX(mail_date) FROM portfolio_mail_links GROUP BY matter"):
        try:
            for mid, stamp in desk.db.execute(sql):
                if mid and stamp and str(stamp) > recent.get(str(mid), ''):
                    recent[str(mid)] = str(stamp)
        except Exception:
            continue
    out = []
    for m in matters:
        terms = [str(m.get('id', '')), str(m.get('client_name', '')), PurePosixPath(str(m.get('path', ''))).name]
        terms += [str(x) for x in (m.get('aliases') or [])] + [str(x) for x in (m.get('references') or [])]
        adversaries = []
        for person in m.get('correspondents', []) or []:
            if not isinstance(person, dict):
                continue
            name = str(person.get('name') or person.get('email') or '')
            if name:
                terms.append(name)
                if person.get('role') in ('confrere_adverse', 'autre_partie'):
                    adversaries.append(name)
        out.append({'id': str(m['id']), 'label': matter_display(m), 'client': str(m.get('client_name', '')),
                    'adversaries': adversaries[:4], 'search': fold(' '.join(t for t in terms if t)), 'recent_at': recent.get(str(m['id']), '')})
    recent_rows = sorted([x for x in out if x['recent_at']], key=lambda x: x['recent_at'], reverse=True)
    others = sorted([x for x in out if not x['recent_at']], key=lambda x: fold(x['label']))
    for x in recent_rows:
        x['recent'] = True
    for x in others:
        x['recent'] = False
    return recent_rows + others


def attachments_listing(desk, idents, matter='', key=''):
    """Liste commune texte–voix des pièces jointes : nom, extraction, pages lisibles."""
    from .improvements36 import attachment_source, attachment_schema
    attachment_schema(desk)
    out = []
    for ident in [str(x) for x in idents if x][:3]:
        try:
            source = attachment_source(desk, ident, matter, key)
        except Stop as ex:
            out.append({'id': ident, 'name': '', 'error': str(ex), 'readable': False})
            continue
        meta = desk.db.execute('SELECT extracted_chars,part_count FROM assistant_attachment_meta_v363 WHERE attachment_id=?', (ident,)).fetchone()
        chars = int(meta['extracted_chars']) if meta else len(source.get('excerpt', ''))
        pages = 0
        for r in desk.db.execute('SELECT excerpt FROM assistant_attachment_parts_v363 WHERE attachment_id=?', (ident,)):
            pages += len(re.findall(r'(?m)^\[Page \d+\]\s*$', r['excerpt'] or ''))
        out.append({'id': ident, 'name': source.get('path', ''), 'chars': chars, 'parts': int(meta['part_count']) if meta else 1,
                    'pages': pages or 1, 'readable': chars > 0, 'partial': bool(source.get('partial'))})
    return out


# ---------------------------------------------------------------------------------------- états d'un document
def document_checks(desk, doc_row, proof):
    """Trois états distincts : dépôt vérifié (relecture SHA-256), contrôle juridique effectué, validation de l'avocat."""
    from .cockpit530 import ensure_schema
    ensure_schema(desk)
    control = proof.get('control') or {}
    validated = None
    stale = False
    if doc_row and doc_row['path']:
        item = 'doc:' + digest(doc_row['path'])[:24]
        validated = desk.db.execute('SELECT decision,at FROM cockpit530_reviewed WHERE item=?', (item,)).fetchone()
        # 5.6.14 (C12) : la validation est attachée au hash du contenu validé ; une nouvelle version la périme
        v = validation_record(desk, item)
        if validated and v and proof.get('sha256') and v['sha256'] and v['sha256'] != proof.get('sha256'):
            stale = True
    deposit = bool(proof.get('sha256')) and proof.get('readback_sha256') == proof.get('sha256')
    outcome = control.get('outcome') or ('reussi' if control.get('done') else 'indisponible')
    labels = {'reussi': 'Contrôle juridique réussi', 'reserves': 'Contrôle juridique avec réserves', 'bloque': 'Contrôle juridique bloquant', 'indisponible': 'Contrôle juridique non effectué'}
    return {'deposit': deposit, 'deposit_label': 'Dépôt vérifié' if deposit else 'Dépôt non vérifié',
            'legal_control': outcome == 'reussi', 'legal_control_outcome': outcome, 'legal_control_execution': control.get('execution', 'executee' if control.get('done') else 'non_executee'),
            'legal_control_label': labels.get(outcome, 'Contrôle juridique non effectué'), 'legal_control_detail': control.get('summary', ''),
            'lawyer_validation': bool(validated and validated['decision'] == 'valide' and not stale),
            'lawyer_validation_label': ('Validation à renouveler (document modifié)' if stale else 'Validé par l’avocat') if validated and validated['decision'] == 'valide'
                                       else ('Écarté par l’avocat' if validated and validated['decision'] == 'ecarte' else 'Validation de l’avocat en attente')}


VALIDATION_SCHEMA = '''CREATE TABLE IF NOT EXISTS validations5614(item TEXT PRIMARY KEY, path TEXT NOT NULL, sha256 TEXT NOT NULL, decision TEXT NOT NULL, at TEXT NOT NULL);'''


def record_validation(desk, item, path, sha256, decision):
    desk.db.executescript(VALIDATION_SCHEMA)
    desk.db.execute('INSERT OR REPLACE INTO validations5614 VALUES(?,?,?,?,?)', (item, path, sha256 or '', decision, desk.now()))
    desk.db.commit()


def validation_record(desk, item):
    desk.db.executescript(VALIDATION_SCHEMA)
    r = desk.db.execute('SELECT * FROM validations5614 WHERE item=?', (item,)).fetchone()
    return dict(r) if r else None


def checks_html(checks):
    def badge(ok, label):
        return '<span class="ws-ai-check %s">%s %s</span>' % ('ok' if ok else 'ko', '✓' if ok else '○', escape(label))
    return ('<p class="ws-ai-checks">%s %s %s</p>' % (badge(checks['deposit'], checks['deposit_label']), badge(checks['legal_control'], checks['legal_control_label']),
                                                   badge(checks['lawyer_validation'], checks['lawyer_validation_label'])))
