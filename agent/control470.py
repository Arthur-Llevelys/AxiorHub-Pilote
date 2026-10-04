"""Contrôle qualité 4.7.0 : mentions obligatoires + vérification des citations, branché sur les deux circuits
de rédaction d'actes (projets de documents et ateliers d'actes). Aucun envoi, aucun dépôt externe."""
import re

from . import templates470, verify470
from .common import Stop


def fact_date_for(desk, matter, args=None):
    value = str((args or {}).get('fact_date') or '').strip()
    if not value:
        value = str(desk.settings('sources470:fact_date:' + str(matter), '') or '')
    if value and not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        raise Stop('date_des_faits_invalide')
    return value


def draft_text(draft):
    parts = [draft.get('title', ''), draft.get('introduction', '')]
    parts += [str(x.get('heading', '')) + '\n' + str(x.get('body', '')) for x in draft.get('sections', [])]
    parts += ['• ' + str(x.get('text', '')) for x in draft.get('requests', [])]
    parts += [str(x.get('label', '')) for x in draft.get('exhibits_referenced', [])]
    return '\n'.join(p for p in parts if p)


def quality(desk, matter, kind, text, args=None):
    """{'mentions': contrôle|None, 'citations': rapport, 'fact_date': ...}"""
    fact_date = fact_date_for(desk, matter, args)
    mentions = templates470.check(desk, kind, text) if kind else None
    try:
        citations = verify470.verify_text(desk, text, fact_date)
    except Stop:
        raise
    except Exception:
        citations = {'items': [], 'counts': {}, 'total': 0, 'needs_check': True, 'truncated': False, 'fact_date': fact_date,
                     'sources': {}, 'headline': 'À VÉRIFIER : contrôle des citations indisponible.', 'error': True}
    return {'mentions': mentions, 'citations': citations, 'fact_date': fact_date}


def apply_quality_controls(desk, data, control, args=None):
    """Complète le contrôle d'un projet de document : mentions manquantes = projet bloqué (non « prêt à relire »)."""
    kind = templates470.kind_for_document(data.get('document_type', ''))
    q = quality(desk, data['matter']['id'], kind, draft_text(data['draft']), args)
    control = dict(control)
    control['citations'] = q['citations']
    control['fact_date'] = q['fact_date']
    if q['mentions'] is not None:
        control['mentions'] = q['mentions']
        if not q['mentions']['ok']:
            control['status'] = 'blocked'
            control['blocking_reasons'] = list(control.get('blocking_reasons', [])) + ['mentions_obligatoires_manquantes']
            control['warnings'] = list(control.get('warnings', [])) + [
                'Mentions obligatoires manquantes : ' + '; '.join(x['label'] for x in q['mentions']['missing'])]
    if q['citations']['needs_check']:
        control['warnings'] = list(control.get('warnings', [])) + ['Références juridiques à vérifier : ' + q['citations']['headline']]
    verify470.store(desk, 'project', data.get('proposal_id', ''), data['matter']['id'], q['citations'])
    return control


def banner(quality_data):
    lines = []
    m = quality_data.get('mentions')
    if m and not m['ok']:
        lines.append('INCOMPLET — mentions obligatoires manquantes : ' + '; '.join(x['label'] for x in m['missing']) + '.')
    c = quality_data.get('citations') or {}
    if c.get('needs_check'):
        bad = [i['label'] + ' (' + i['status_label'].lower() + ')' for i in c.get('items', []) if i['status'] != 'verifiee'][:10]
        lines.append('À VÉRIFIER — références juridiques non confirmées : ' + ('; '.join(bad) or 'contrôle incomplet') + '.')
    return '\n'.join(lines)
