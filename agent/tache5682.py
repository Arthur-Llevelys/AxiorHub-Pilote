"""5.6.82 : profil de tâche (TaskProfiler). Déduit des données structurées et de l'étape, sans appel au modèle.

La longueur ne détermine pas la difficulté : une extraction reste une extraction, même sur un long document, et un courriel court peut
poser une question stratégique. Une source manquante n'est pas une difficulté de raisonnement : un modèle plus puissant ne reconstitue
pas une preuve absente, le passage reste à compléter. Le contenu d'une pièce ne modifie ni la politique ni les outils permis : seules les
consignes de l'avocat et les champs de tri sont lus pour ajuster le niveau, jamais vers moins de contrôle.
"""
import json
import re

from .common import fold

NIVEAUX = ('econome', 'standard', 'avance', 'maximal')
EFFORTS = ('low', 'medium', 'high')
EFFORT_LABELS = {'low': 'faible', 'medium': 'standard', 'high': 'renforcé', '': 'non réglable'}
# étape → (type de tâche, niveau, effort, risque)
STAGES = {
    'triage': ('classement', 'econome', 'low', 'faible'), 'mail_orchestration_classification': ('classement', 'econome', 'low', 'faible'),
    'attachment_review': ('extraction', 'econome', 'low', 'faible'), 'deadline_review': ('extraction', 'econome', 'low', 'moyen'),
    'memory_insight': ('extraction', 'econome', 'low', 'faible'), 'case_brief': ('synthese', 'standard', 'medium', 'moyen'),
    'dossier_fiche': ('synthese', 'standard', 'medium', 'moyen'), 'legal_memory': ('synthese', 'standard', 'medium', 'moyen'),
    'meeting_preparation': ('synthese', 'standard', 'medium', 'moyen'), 'transcript_report': ('synthese', 'standard', 'medium', 'moyen'),
    'compose': ('redaction_mail', 'standard', 'medium', 'moyen'), 'manual_draft': ('redaction_mail', 'standard', 'medium', 'moyen'),
    'desk_review': ('redaction_mail', 'standard', 'medium', 'moyen'), 'chat': ('conversation', 'standard', 'medium', 'moyen'),
    'verify': ('controle', 'standard', 'medium', 'moyen'), 'preparation_control': ('controle', 'standard', 'medium', 'moyen'),
    'document_control': ('controle', 'avance', 'medium', 'eleve'), 'opinion_control': ('controle', 'avance', 'high', 'eleve'),
    'second_model_review': ('controle', 'avance', 'medium', 'eleve'), 'mail_case_differential': ('analyse_juridique', 'avance', 'medium', 'eleve'),
    'strategy_analysis': ('analyse_juridique', 'avance', 'high', 'eleve'), 'evidence_matrix': ('analyse_juridique', 'avance', 'high', 'eleve'),
    'legal_opinion_simulation': ('analyse_juridique', 'avance', 'high', 'eleve'), 'hearing_preparation': ('analyse_juridique', 'avance', 'high', 'eleve'),
    'act_project': ('redaction_acte', 'avance', 'medium', 'eleve'), 'document_project': ('redaction_acte', 'avance', 'medium', 'eleve'),
    'word_revision_plan': ('revision_document', 'avance', 'medium', 'eleve'),
}
PURPOSES = {'mail_triage': ('classement', 'econome', 'low', 'faible'), 'attachment_review': ('extraction', 'econome', 'low', 'faible'),
            'mail_drafting': ('redaction_mail', 'standard', 'medium', 'moyen'), 'assistant': ('conversation', 'standard', 'medium', 'moyen'),
            'voice_conversation': ('conversation', 'standard', 'low', 'moyen'), 'legal_analysis': ('analyse_juridique', 'avance', 'high', 'eleve'),
            'hearing': ('analyse_juridique', 'avance', 'high', 'eleve'), 'document_drafting': ('redaction_acte', 'avance', 'medium', 'eleve'),
            'roundcube': ('redaction_mail', 'standard', 'medium', 'moyen'), 'control': ('controle', 'standard', 'medium', 'moyen')}
TASK_LABELS = {'classement': 'classement', 'extraction': 'extraction', 'synthese': 'synthèse', 'redaction_mail': 'rédaction d’un courriel',
               'conversation': 'réponse à une question', 'controle': 'contrôle', 'analyse_juridique': 'analyse juridique',
               'redaction_acte': 'rédaction d’un acte', 'revision_document': 'révision d’un document'}
NEVER_RAISED = ('classement', 'extraction', 'controle')        # une consigne ou une pièce ne fait pas monter ces travaux
STRATEGIC = re.compile(r'strateg|transaction|transiger|concession|negociat|mise en demeure|assignation|conclusions|appel\b|pourvoi|prescription|'
                       r'forclusion|nullite|resolution du contrat|responsabilite|licenciement|penal|garantie|contradict|risque|faut-il|que conseillez|'
                       r'dois-je accepter|devons-nous', re.I)
OWN_FIELDS = ('question', 'question_avocat', 'instruction', 'instruction_avocat', 'objectif', 'consigne', 'incoming')
LIST_KEYS = ('sources', 'source_ids', 'factual_sources', 'legal_sources', 'documents', 'pieces', 'attachments', 'files')


def _walk(value, key_set, depth=0):
    count = 0
    if isinstance(value, dict) and depth < 8:
        for k, v in value.items():
            if k in key_set and isinstance(v, list):
                count += len(v)
            else:
                count += _walk(v, key_set, depth + 1)
    elif isinstance(value, list) and depth < 8:
        for v in value:
            count += _walk(v, key_set, depth + 1)
    return count


def _contradictions(value, depth=0):
    if isinstance(value, dict) and depth < 6:
        for k, v in value.items():
            if k in ('contradictions', 'contradicting_source_ids') and v:
                return True
            if _contradictions(v, depth + 1):
                return True
    return False


def _own_text(payload, purpose):
    if not isinstance(payload, dict):
        return ''
    parts = [str(payload[k])[:2000] for k in OWN_FIELDS if isinstance(payload.get(k), str)]
    return fold(' '.join(parts))


def _tokens(payload, messages):
    try:
        text = json.dumps(messages if messages is not None else payload, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        text = str(messages or payload or '')
    return max(1, len(text) // 3)


def profile(purpose, stage='', payload=None, messages=None, preference='auto', matter='', owner='', budget=None, needs_json=False):
    """TaskProfile : ce qu'il faut savoir pour choisir, sans le texte du dossier (rien de ce dictionnaire n'est un extrait)."""
    task, level, effort, risk = STAGES.get(stage) or PURPOSES.get(purpose) or ('conversation', 'standard', 'medium', 'moyen')
    reasons = ['base_' + task]
    sources = _walk(payload, set(LIST_KEYS))
    contradictions = _contradictions(payload)
    triage = payload.get('triage') if isinstance(payload, dict) and isinstance(payload.get('triage'), dict) else {}
    intent = str(triage.get('intent') or (payload.get('intent') if isinstance(payload, dict) else '') or '')
    text = _own_text(payload, purpose)
    if task not in NEVER_RAISED:
        if intent in ('legal', 'strategy') or STRATEGIC.search(text):
            level, risk = max(level, 'avance', key=NIVEAUX.index), 'eleve'
            effort = max(effort, 'medium', key=EFFORTS.index)
            reasons.append('question_strategique')
        if contradictions:
            level, risk = max(level, 'avance', key=NIVEAUX.index), 'eleve'
            effort = 'high'
            reasons.append('contradiction_a_verifier')
        if sources >= 8 and task in ('conversation', 'redaction_mail', 'synthese'):
            level = max(level, 'avance', key=NIVEAUX.index)
            reasons.append('plusieurs_pieces')
    missing = isinstance(payload, dict) and task in ('redaction_mail', 'conversation') and sources == 0 and any(k in payload for k in ('sources', 'source_ids'))
    if missing:
        reasons.append('sources_manquantes')
    if preference == 'approfondi' and task not in ('classement',):
        effort = EFFORTS[min(EFFORTS.index(effort) + 1, 2)]
        if level == 'standard':
            level = 'avance'
        reasons.append('preference_approfondie')
    elif preference == 'rapide':
        effort = 'low'
        if level == 'avance' and risk != 'eleve':
            level = 'standard'
        reasons.append('preference_rapide')
    return {'proprietaire': str(owner or '')[:120], 'dossier': str(matter or ''), 'fonction': purpose, 'etape': stage, 'type_tache': task,
            'risque': risk, 'capacites': ['sortie_structuree'] if needs_json else [], 'jetons_estimes': _tokens(payload, messages),
            'sources': sources, 'contradictions': contradictions, 'sources_manquantes': bool(missing), 'delai': preference,
            'budget_restant': budget, 'niveau': level, 'effort': effort, 'raisons': reasons}


def short_reason(prof, escalated=False):
    """Phrase courte pour le détail « Choix de l'IA » ; ce n'est pas la chaîne de raisonnement du modèle."""
    label = TASK_LABELS.get(prof['type_tache'], 'travail')
    reasons = prof['raisons']
    if escalated:
        return 'Niveau supérieur retenu après un contrôle non satisfaisant (%s).' % label
    why = []
    if 'plusieurs_pieces' in reasons:
        why.append('plusieurs pièces')
    if 'contradiction_a_verifier' in reasons:
        why.append('une contradiction à vérifier')
    if 'question_strategique' in reasons:
        why.append('une question de stratégie ou de droit')
    if why:
        return 'Analyse approfondie retenue : ' + ' et '.join(why) + '.'
    if 'sources_manquantes' in reasons:
        return 'Pièces manquantes : le passage concerné reste à compléter, un modèle plus puissant ne les remplacerait pas.'
    if 'preference_approfondie' in reasons:
        return 'Analyse approfondie demandée (%s).' % label
    if 'preference_rapide' in reasons:
        return 'Réponse rapide demandée (%s).' % label
    return {'econome': 'Tâche ciblée (%s) : modèle économe.', 'standard': 'Travail courant (%s) : modèle standard.',
            'avance': 'Travail complexe (%s) : modèle avancé.', 'maximal': 'Travail le plus exigeant (%s).'}[prof['niveau']] % label
