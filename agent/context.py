"""Bounded mail context. No generated summary and no truncation of the incoming mail."""
import copy
import json
import re

from .common import Stop


def canonical(text):
    # Only quote prefixes and whitespace change; case, words and punctuation remain.
    def unquote(match):
        rest = match[1]
        # A leading comparison such as '> 100 euros' is ambiguous: retain it.
        if re.match(r'[\d=+\-<>≤≥]', rest):
            return match[0]
        return rest
    text = re.sub(r'(?m)^[ \t]*(?:>[ \t]*)+([^\n]*)$', unquote, text)
    return re.sub(r'\s+', ' ', text).strip()


def contained(needle, haystack):
    offset = 0
    while True:
        start = haystack.find(needle, offset)
        if start < 0:
            return False
        end = start + len(needle)
        before = haystack[start-1:start] if start else ''
        after = haystack[end:end+1]
        if not (before.isalnum() and needle[0].isalnum()) and not (
                after.isalnum() and needle[-1].isalnum()) and not re.search(
                r'[><=≤≥]\s*$', haystack[:start]):
            return True
        offset = start + 1


def compact_history(incoming, history):
    quoted = canonical(incoming['text'])
    kept, repeated, partial = [], [], []
    for item in history:
        text = item['text']
        normalized = canonical(text)
        if len(normalized) >= 120 and contained(normalized, quoted):
            repeated.append({k: item[k] for k in ('id', 'sender', 'received_at')})
            continue
        # Drop only entire paragraphs whose full wording occurs in the incoming
        # message. Unique content, including negations and amounts, is retained.
        blocks = re.split(r'\n[ \t>]*\n', text)
        output, removed, last_removed = [], 0, False
        for block in blocks:
            normalized = canonical(block)
            if len(normalized) >= 120 and contained(normalized, quoted):
                removed += len(block)
                if not last_removed:
                    output.append('[Passage déjà cité dans incoming : consulter ce courriel. '
                                  'Cette répétition ne constitue pas une corroboration indépendante.]')
                last_removed = True
            else:
                output.append(block)
                last_removed = False
        replacement = '\n\n'.join(output)
        if removed and len(replacement) < len(text):
            kept.append({**item, 'text': replacement, 'repeated_passages_in': 'incoming'})
            partial.append(item['id'])
        else:
            kept.append(item)
    return kept, {'incoming_characters': len(incoming['text']),
                  'incoming_preserved_in_full': True, 'history_messages': len(history),
                  'history_retained': len(kept), 'history_already_quoted': repeated,
                  'history_with_repeated_paragraphs': partial}


def payload_size(payload):
    return len(json.dumps(payload, ensure_ascii=False))


def history_coverage(available, selected):
    return {'available_messages': available, 'selected_messages': selected,
            'omitted_messages': available-selected, 'not_exhaustive': selected < available,
            'selection': 'most_recent_whole_messages_that_fit',
            'instruction': "L'historique omis n'a pas été analysé. Si la demande exige ces échanges, "
                           "signaler une information manquante ; ne pas déduire leur contenu ou une absence."}


def fit_triage(incoming, history, limit):
    result = {'incoming': incoming, 'history': [],
              'history_coverage': history_coverage(len(history), 0)}
    if payload_size(result) > limit:
        raise Stop('courriel_entrant_depasse_contexte')
    selected, omitted = [], []
    for i in reversed(range(len(history))):
        proposal = sorted(selected+[i])
        candidate = {**result, 'history': [history[j] for j in proposal],
                     'history_coverage': history_coverage(len(history), len(proposal))}
        if payload_size(candidate) <= limit:
            result, selected = candidate, proposal
        else:
            omitted.append(history[i]['id'])
    return result, omitted


def fit_context(payload, limit, reserve=9000):
    """Retain incoming, attachments, calendar and best document; select history.

    History is selected by recency, whole message at a time, and missing history
    is disclosed to triage, composition and verification. No IA summary is made.
    """
    result = copy.deepcopy(payload)
    original = result['sources']
    documents = [s for s in original if s['kind']=='document']
    history = [s for s in original if s['kind']=='email_history']
    memories = [s for s in original if s['kind']=='sent_example']
    selected = {s['id'] for s in original if s['kind'] not in ('document','email_history','sent_example')}
    if documents:
        selected.add(documents[0]['id'])

    def candidate(ids):
        count = sum(s['id'] in ids for s in history)
        docs_count = sum(s['id'] in ids for s in documents)
        return {**result, 'sources': [s for s in original if s['id'] in ids],
                'history_coverage': history_coverage(len(history), count),
                'coverage': {**result.get('coverage', {}), 'selected_files': docs_count,
                             'not_exhaustive': True,
                             'documents_omitted_for_context': len(documents)-docs_count}}

    if payload_size(candidate(selected)) + reserve > limit:
        raise Stop('contexte_essentiel_trop_long')
    for source in memories + list(reversed(history)) + documents[1:]:
        proposed = selected | {source['id']}
        if payload_size(candidate(proposed)) + reserve <= limit:
            selected = proposed
    result = candidate(selected)
    return result, {'history_source_ids': [s['id'] for s in history if s['id'] not in selected],
                    'document_source_ids': [s['id'] for s in documents if s['id'] not in selected]}


def fit_documents(payload, limit, reserve=9000):
    """Remove least-ranked whole document excerpts only; disclose the selection.

    The incoming mail, unique history, attachments and calendar data are never
    cut to fit. A remaining overflow produces a review, not a partial reading.
    Reserve room for the subsequent verification's proposed body and slot data.
    """
    result = copy.deepcopy(payload)
    removed = []
    documents = [s for s in result['sources'] if s['kind'] == 'document']
    while payload_size(result) + reserve > limit and len(documents) > 1:
        source = documents.pop()
        result['sources'] = [s for s in result['sources'] if s['id'] != source['id']]
        removed.append(source['id'])
        result['coverage'] = {**result.get('coverage', {}),
                              'selected_files': len(documents),
                              'not_exhaustive': True,
                              'documents_omitted_for_context': len(removed)}
    if payload_size(result) + reserve > limit:
        raise Stop('contexte_unique_trop_long')
    return result, removed
