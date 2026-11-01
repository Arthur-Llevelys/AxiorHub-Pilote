"""5.6.43 (cahier v9, § 7 et 26.1) : compétences transmises par phase, avec la vraie consigne et un assemblage unique.

- **Vraie consigne** : un travail ne porte souvent qu'un identifiant (``docrequest520`` : identifiant de la demande ; ``task5614`` : mission
  et tâche ; missions : identifiant). La consigne réelle est relue dans sa table (droits du propriétaire conservés par l'appelant) ; les
  données citées d'un courriel ou d'une pièce restent des données et ne remplacent pas la consigne.
- **Phase** : décision, analyse, rédaction d'un courriel, rédaction d'un document, contrôle — déduite du type de travail. Une méthode
  de phase au plus (compétence dont les phases déclarées ou les situations d'emploi correspondent), puis au plus une spécialisation
  retenue par mots-clés sur la vraie consigne. Les cinq méthodes ne sont jamais envoyées ensemble.
- **Assemblage unique** : méthode de phase, spécialisation, puis anciennes extensions (registre 3.6.4), sans doublon et dans UN budget ;
  une instruction qui ne tient pas n'est pas coupée au milieu : elle n'est pas transmise et la limite est notée.
"""
import json
import re

from .common import fold

UNIFIED_BUDGET = 24_000          # caractères (≈ 8 000 jetons) pour TOUTES les instructions de méthode d'un appel
PHASES = ('decision', 'analyse', 'redaction_courriel', 'redaction_document', 'controle')
PHASE_LABELS = {'decision': 'décision', 'analyse': 'analyse', 'redaction_courriel': 'rédaction d’un courriel',
                'redaction_document': 'rédaction d’un document', 'controle': 'contrôle'}
PHASE_TAGS = {
    'decision': ('decision', 'cadrage', 'triage', 'diligence', 'quoi preparer'),
    'analyse': ('analyse', 'preuve', 'chronologie', 'argument', 'contradiction', 'pieces'),
    'redaction_courriel': ('courriel', 'mail', 'reponse', 'relance', 'destinataire'),
    'redaction_document': ('document', 'redaction', 'contrat', 'conclusions', 'consultation', 'assignation', 'modele word'),
    'controle': ('controle', 'relecture', 'verification', 'coherence', 'omission'),
}
KIND_PHASE = {
    # décision : que faut-il préparer ?
    'orchestrate': 'decision', 'proactive34_cycle': 'decision', 'propose_work_plan': 'decision', 'initiative5636': 'decision',
    # analyse
    'refresh_brief': 'analyse', 'sync_legal_memory': 'analyse', 'attachment_review': 'analyse', 'analyser_dossier5624': 'analyse',
    'fiche_dossier5627': 'analyse', 'digest_pieces5632': 'analyse', 'legal_research': 'analyse', 'prepare_hearing': 'analyse',
    # rédaction d'un courriel
    'prepare_reply': 'redaction_courriel', 'prepare_draft': 'redaction_courriel', 'run': 'redaction_courriel',
    'live_mail430': 'redaction_courriel', 'maildraft5613': 'redaction_courriel',
    'assistant_answer': 'analyse',
    # rédaction d'un document
    'docrequest520': 'redaction_document', 'prepare_document_project': 'redaction_document', 'prepare_word_project': 'redaction_document',
    'prepare_legal_opinion': 'redaction_document', 'prepare_cabinet_letter': 'redaction_document', 'corporate5636': 'redaction_document',
    'routine520': 'redaction_document', 'recit5651': 'redaction_document',
    # contrôle
    'verify_deliverable420': 'controle', 'verify_official_decision': 'controle',
}
# 5.6.86 : travaux demandés par l'avocat qui suivent la compétence générale du cabinet. Les parcours automatiques fréquents (surveillance, tri) n'en font pas partie :
# ils coûteraient des appels plus lourds à chaque passage. Discussion : section utile seulement, pour rester rapide.
GENERAL_SETTING = 'competences5686:generale'
GENERAL_DEFAULT = 'assistant juridique'
GENERAL_KINDS = frozenset(('prepare_reply', 'prepare_draft', 'maildraft5613', 'assistant_answer', 'docrequest520', 'prepare_document_project', 'prepare_word_project',
                           'prepare_legal_opinion', 'prepare_cabinet_letter', 'task5614', 'initiative5636', 'routine520', 'recit5651'))
CHAT_BUDGET = 12_000
TASK_TYPE_PHASE = (('controle', 'controle'), ('verif', 'controle'), ('relecture', 'controle'), ('analyse', 'analyse'),
                   ('recherche', 'analyse'), ('courriel', 'redaction_courriel'), ('mail', 'redaction_courriel'),
                   ('redaction', 'redaction_document'), ('document', 'redaction_document'), ('acte', 'redaction_document'),
                   ('decision', 'decision'), ('plan', 'decision'))


def real_instruction(desk, kind, args):
    """(consigne réelle, type de livrable, phase, propriétaire) relus en base ; à défaut, le texte porté par les arguments."""
    args = args if isinstance(args, dict) else {}
    text, deliverable, phase = '', '', KIND_PHASE.get(kind, '')
    try:
        if kind == 'docrequest520' and args.get('request'):
            row = desk.db.execute('SELECT request,kind FROM docreq520 WHERE id=?', (str(args['request']),)).fetchone()
            if row:
                text, deliverable = row['request'], row['kind']
        elif kind == 'task5614' and args.get('task'):
            row = desk.db.execute('SELECT t.title,t.instruction,t.task_type,t.role,m.objective FROM tasks5614 t JOIN missions5614 m ON m.id=t.mission '
                                  'WHERE t.id=? AND t.mission=?', (str(args['task']), str(args.get('mission') or ''))).fetchone()
            if row:
                text = '\n'.join(x for x in (row['objective'], row['title'], row['instruction']) if x)
                deliverable = row['task_type']
                hay = fold(row['task_type'] + ' ' + row['role'])
                phase = next((p for key, p in TASK_TYPE_PHASE if key in hay), 'redaction_document')
        elif args.get('mission_id'):
            row = desk.db.execute('SELECT instruction,kind FROM missions_v567 WHERE id=?', (str(args['mission_id']),)).fetchone()
            if row:
                text, deliverable = row['instruction'], row['kind']
                phase = phase or {'mail': 'redaction_courriel', 'document': 'redaction_document', 'question': 'analyse'}.get(row['kind'], '')
    except Exception:   # noqa: BLE001 — table absente sur une installation partielle : repli sur les arguments
        pass
    if not text:
        from .competences5632 import TEXT_KEYS
        text = ' '.join(str(args.get(k) or '') for k in TEXT_KEYS if k != 'request' or kind != 'docrequest520')
    deliverable = deliverable or str(args.get('document_type') or args.get('kind') or '')
    if not phase and deliverable:
        phase = 'redaction_document'
    return text.strip(), deliverable, phase


def skill_phases(manifest):
    """Phases déclarées (champ ``phases``) ou déduites des situations d'emploi."""
    declared = manifest.get('phases') or []
    if isinstance(declared, str):
        declared = [x.strip() for x in re.split(r'[,;]', declared) if x.strip()]
    found = [p for p in declared if p in PHASES]
    if found:
        return found
    hay = ' ' + fold(' '.join(str(x) for x in (manifest.get('situations') or []) + [manifest.get('name', '')])) + ' '
    scores = []
    for phase, tags in PHASE_TAGS.items():
        n = sum(1 for t in tags if re.search(r'(?<![a-z0-9])' + re.escape(t) + r'(?![a-z0-9])', hay))
        if n:
            scores.append((n, phase))
    scores.sort(reverse=True)
    return [scores[0][1]] if scores and (len(scores) == 1 or scores[0][0] > scores[1][0]) else []


def general_skill(desk, rows):
    """Compétence générale du cabinet : celle choisie dans la page Compétences, sinon « Assistant juridique » si elle est active. Choix vide : aucune."""
    chosen = desk.settings(GENERAL_SETTING, None)
    if chosen == '':
        return None
    return next((s for s in rows if s['id'] == chosen), None) if chosen else next((s for s in rows if fold(s['name']) == GENERAL_DEFAULT), None)


def select(desk, text, phase, owner='cabinet', manual=None, auto=None, kind=''):
    """Méthode de phase (une au plus) puis spécialisation par mots-clés (une au plus). Le choix manuel de l'avocat prime.

    5.6.86 : une méthode qui déclare sa phase passe d'abord, puis la compétence générale pour les travaux demandés par l'avocat, puis une phase déduite des mots-clés. Avant, une
    compétence spécialisée (comptes annuels) pouvait servir de méthode à des travaux sans rapport, et une compétence générale ne servait jamais aux réponses ni à la discussion."""
    from . import competences5632 as c
    if manual:
        return c.resolve(desk, text, owner, manual=manual)
    if auto is False or (auto is None and not c.auto_mode(desk)):
        return []
    caps = c.available_capabilities(desk)
    rows = [s for s in c.listing(desk, owner, caps) if s['enabled'] and not s['missing']]
    chosen, methods, declared = [], set(), set()
    if phase:
        for s in rows:
            manifest = json.loads(c._row(desk, s['id'], s['version'])['manifest'])
            if phase in skill_phases(manifest):
                methods.add(s['id'])
                if phase in (manifest.get('phases') or []):
                    declared.add(s['id'])   # une méthode qui déclare sa phase prime sur une phase déduite des mots-clés
        general = general_skill(desk, rows) if kind in GENERAL_KINDS else None
        if declared:
            method = sorted(declared)[0]
        elif general:
            method = general['id']
            methods.add(method)
        else:
            method = sorted(methods)[0] if methods and phase != 'decision' else None     # décider quoi préparer : uniquement une méthode faite pour cela
        if method:
            s = next(x for x in rows if x['id'] == method)
            chosen.append({'id': s['id'], 'version': s['version'], 'mode': 'phase', 'reason': 'Méthode de la phase « %s »' % PHASE_LABELS[phase]})
    for extra in c.resolve(desk, text, owner, auto=True):
        if extra['id'] in methods or any(x['id'] == extra['id'] for x in chosen):
            continue     # pas une deuxième méthode de phase
        chosen.append({**extra, 'mode': 'specialisation'})
        break
    return chosen[:c.MAX_SELECTED]


def _blocks(text):
    return [b.strip() for b in re.split(r'\n(?=COMPÉTENCE « |Extension )', str(text or '')) if b.strip()]


def assemble(legacy, selected, budget=UNIFIED_BUDGET):
    """Une seule chaîne d'instructions : compétences retenues d'abord, puis extensions historiques ; doublons retirés ; blocs entiers
    seulement, dans un budget unique ; les blocs non transmis sont signalés (jamais coupés au milieu de leurs limites ou contrôles)."""
    out, used, seen, skipped = [], 0, set(), 0
    for block in _blocks(selected) + _blocks(legacy):
        key = re.sub(r'\s+', ' ', fold(block))[:4000]
        if key in seen:
            continue
        seen.add(key)
        if used + len(block) > budget:
            from .competences5683 import trim_block     # 5.6.83 (SK03) : sections entières dans la place restante, plutôt qu'un refus global
            kept = trim_block(block, budget - used) if block.startswith('COMPÉTENCE « ') else ''
            if not kept:
                skipped += 1
                continue
            block = kept
        out.append(block)
        used += len(block)
    if skipped:
        out.append('[%d instruction(s) de méthode non transmise(s) faute de place dans le contexte : elles ne sont pas appliquées à cet appel.]'
                   % skipped)
    return '\n\n'.join(out)
