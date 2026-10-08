"""5.6.14 (U01, U02) : barre des routines en tête d'« Aujourd'hui » et centre « À décider » compact et actionnable.

- Routines : une barre compacte sous le titre (briefing, tri, bilan, documents à préparer) avec dernier lancement, état, accès au
  résultat, lancement sans double clic, et une action « Pause des automatismes » visible.
- À décider : un résumé replié par défaut (total, urgentes), un panneau latéral de largeur raisonnable ; chaque carte porte le
  contexte, la question précise, la recommandation, les sources, les conséquences et des actions Valider, Modifier, Compléter les
  instructions, Reporter, Annuler. Les décisions des missions complexes, des missions, des documents, des agents documentaires, des
  engagements, des courriels à rattacher et des règles proposées sont réunies.
"""
from datetime import datetime, timedelta, timezone
from html import escape as e
import json

from .common import load_matters, matter_display

ROUTINES = (('briefing', 'Briefing'), ('tri', 'Tri'), ('bilan', 'Bilan'), ('docs', 'Documents à préparer'))
URGENT_KINDS = {'validation', 'reserve', 'champs_manquants', 'profil_ambigu', 'profil_hors_couverture', 'ecritures_ambigues', 'budget'}


def _local(desk, stamp):
    try:
        from .cockpit530 import _local as loc
        return loc(desk, stamp, '%d/%m %H:%M')
    except Exception:
        return str(stamp)[:16].replace('T', ' ')


def routines_bar_html(desk, prefix):
    from . import routines520 as r5
    parts = []
    for kind, label in ROUTINES:
        if kind == 'docs':
            try:
                n = len(r5.documents(desk))
            except Exception:
                n = 0
            state = '%d en attente' % n
            when = ''
        else:
            rep = None
            try:
                rep = r5.latest(desk, kind)
            except Exception:
                rep = None
            when = _local(desk, rep['created']) if rep else ''
            state = 'produit' if rep else 'pas encore produit'
        running = desk.db.execute("SELECT 1 FROM jobs WHERE kind='routine520' AND status IN ('pending','running') AND json_valid(args) AND json_extract(args,'$.kind')=?", (kind,)).fetchone()
        if running:
            state = 'en cours'
        action = ('<button type="button" class="ax-btn ghost c5614-run" data-routine="%s"%s>Lancer</button>' % (kind, ' disabled' if running else '')) if kind != 'docs' else ''
        parts.append('<div class="c5614-routine" data-kind="%s"><strong>%s</strong><span class="c5614-state">%s%s</span><span class="c5614-actions">%s'
                     '<button type="button" class="ax-btn ghost c5614-view" data-tab-open="%s">Voir</button></span></div>' % (
            kind, e(label), e(state), (' · ' + e(when)) if when else '', action, kind))
    paused = bool(desk.settings('missions567:pause', False))
    return ('<section class="c5614-routines" id="c5614-routines" aria-label="Routines du cabinet"><div class="c5614-bar">%s</div>'
            '<div class="c5614-side"><button type="button" class="ax-btn %s" id="c5614-pause" data-paused="%s" aria-pressed="%s">%s</button>'
            '<a class="ax-btn ghost" href="%s">Réglages</a></div><p id="c5614-routines-status" role="status" class="c530-sr"></p></section>') % (
        ''.join(parts), 'warn' if paused else 'ghost', '1' if paused else '0', 'true' if paused else 'false',
        'Automatismes en pause — reprendre' if paused else 'Pause des automatismes', e(prefix + '/aujourdhui?vue=essentiel', quote=True))


def _deferred(desk):
    raw = desk.settings('decisions5614:deferred', {}) or {}
    now = datetime.now(timezone.utc).isoformat()
    return {k: v for k, v in raw.items() if str(v) > now}


def defer(desk, ident, days=1):
    raw = dict(desk.settings('decisions5614:deferred', {}) or {})
    until = (datetime.now(timezone.utc) + timedelta(days=max(1, min(int(days), 30)))).replace(hour=7, minute=0, second=0, microsecond=0).isoformat()
    raw[str(ident)] = until
    desk.setting('decisions5614:deferred', raw)
    return {'id': ident, 'until': until}


def items(desk, owner='cabinet', prefix=''):
    """Toutes les décisions attendues, normalisées : kind, id, title, matter_label, question, recommendation, sources, consequence, urgent, actions."""
    from . import decisions569, taches5614
    deferred = _deferred(desk)
    out = []
    for x in decisions569.items(desk, owner, prefix):
        key = x['kind'] + ':' + str(x['id'])
        if key in deferred:
            continue
        card = {'key': key, 'kind': x['kind'], 'id': x['id'], 'state': x['state'], 'title': x['title'], 'matter': x['matter'], 'matter_label': x['matter_label'],
                'question': x['explanation'], 'candidates': x['candidates'], 'url': x['url'], 'urgent': x['kind'] in ('docreq', 'mission') and x['state'] in ('echec', 'error'),
                'recommendation': '', 'sources': [], 'consequence': ''}
        if x['kind'] == 'mission':
            card['recommendation'] = 'Précisez le dossier puis démarrez ; ou complétez l’instruction avant de relancer.' if x['state'] == 'decision' else 'Relancez après correction de la cause, ou annulez.'
            card['consequence'] = 'Sans décision, aucune préparation ne démarre pour cette mission.'
        elif x['kind'] == 'docreq':
            card['recommendation'] = 'Choisissez le dossier dans lequel rédiger.' if x['state'] == 'dossier_a_choisir' else 'Relancez la demande ; le motif d’échec est indiqué.'
            card['consequence'] = 'Le document ne sera pas rédigé tant que le dossier n’est pas choisi.' if x['state'] == 'dossier_a_choisir' else 'Le document reste absent du dossier.'
        elif x['kind'] == 'document':
            card['recommendation'] = 'Confirmez le dossier suggéré ou la date de création prouvée.'
            card['consequence'] = 'Le document reste dans le dossier d’arrivée, non classé.'
        elif x['kind'] == 'mails':
            card['recommendation'] = 'Rattachez les expéditeurs et dossiers : les brouillons suivants seront préparés.'
            card['consequence'] = 'Aucun brouillon n’est préparé pour ces courriels.'
        elif x['kind'] == 'rule':
            card['recommendation'] = 'Adoptez la règle pour le cabinet ou ce dossier seulement, ou ignorez-la.'
            card['consequence'] = 'Adoptée, elle s’applique aux prochaines rédactions (réversible dans Règles métier).'
        elif x['kind'] == 'plan':
            card['recommendation'] = 'Reprenez la suite après avoir levé le blocage.'
        elif x['kind'] == 'commitment':
            card['recommendation'] = 'Confirmez la condition ou précisez le dossier et l’échéance.'
        out.append(card)
    for d in taches5614.pending_decisions(desk, owner, prefix):
        card = {'key': 'mission5614:' + d['id'], 'kind': 'mission5614', 'id': d['id'], 'mission': d['mission'], 'state': d['kind'], 'title': d['label'] + ' — ' + d['title'],
                'matter': d['matter'], 'matter_label': matter_display(next((m for m in load_matters(desk.c) if m['id'] == d['matter']), {'id': d['matter']})),
                'question': d['question'], 'candidates': [], 'url': d['url'], 'urgent': d['kind'] in URGENT_KINDS, 'payload': d['payload'], 'decision_kind': d['kind'],
                'recommendation': '', 'sources': [], 'consequence': ''}
        p = d['payload']
        if d['kind'] == 'validation':
            card['recommendation'] = 'Relisez le projet puis validez cette version (hash %s) ; en cas de réserve, demandez une correction ciblée.' % str(p.get('sha256', ''))[:12]
            card['sources'] = [p.get('path', '')] + [str(r) for r in p.get('reserves', [])][:5]
            card['consequence'] = 'La validation porte sur ce contenu précis ; toute nouvelle version la révoque.'
        elif d['kind'] == 'champs_manquants':
            card['recommendation'] = 'Renseignez les champs ; les lectures et inventaires continuent pendant ce temps.'
            card['consequence'] = p.get('consequence', '')
        elif d['kind'] in ('profil_ambigu', 'profil_hors_couverture'):
            card['recommendation'] = 'Choisissez la procédure applicable ; chaque option indique sa conséquence.'
            card['consequence'] = 'L’en-tête, les mentions et le modèle Word dépendent de ce choix.'
        elif d['kind'] == 'reserve':
            card['recommendation'] = 'Ouvrez le défaut et demandez sa correction, ou validez avec une réserve motivée.'
            card['sources'] = [str(x.get('localisation', '')) + ' — ' + str(x.get('message', x.get('regle_ou_source', '')))[:120] for x in p.get('defects', [])][:6]
            card['consequence'] = 'Le projet reste un projet de travail tant que le blocage demeure.'
        elif d['kind'] == 'budget':
            card['recommendation'] = 'Augmentez le budget d’appels ou découpez la mission ; aucun résultat n’est perdu.'
        elif d['kind'] == 'ecritures_ambigues':
            card['recommendation'] = 'Sélectionnez nos dernières conclusions et les dernières adverses.'
            card['sources'] = [c.get('name', '') + ' (' + c.get('modified', '')[:10] + ')' for c in p.get('candidates', [])][:8]
        elif d['kind'] == 'role_desactive':
            card['recommendation'] = 'Activez le rôle dans Initiatives, ou annulez la mission.'
        out.append(card)
    out.sort(key=lambda c: (not c['urgent'], c['kind']))
    return out


def _options(labels, candidates, current):
    known = {c['id'] for c in candidates}
    sugg = ''.join('<option value="%s"%s>%s (suggéré)</option>' % (e(c['id'], quote=True), ' selected' if c['id'] == current else '', e(c.get('label', c['id']))) for c in candidates)
    rest = ''.join('<option value="%s"%s>%s</option>' % (e(k, quote=True), ' selected' if k == current and k not in known else '', e(v)) for k, v in sorted(labels.items(), key=lambda kv: kv[1].casefold()) if k not in known)
    return '<option value="">Dossier…</option>' + (('<optgroup label="Suggérés">' + sugg + '</optgroup>') if sugg else '') + '<optgroup label="Tous les dossiers">' + rest + '</optgroup>'


def card_html(desk, c, labels):
    head = '<header><strong>%s</strong>%s%s</header>' % (e(c['title']), (' <span class="c530-sub">%s</span>' % e(c['matter_label'])) if c['matter_label'] else '',
                                                          ' <span class="c5614-urgent">urgent</span>' if c['urgent'] else '')
    body = '<p class="c5614-q">%s</p>' % e(c['question'])
    if c['recommendation']:
        body += '<p class="c5614-reco"><em>Recommandation :</em> %s</p>' % e(c['recommendation'])
    if c['sources']:
        body += '<details><summary>Sources et éléments</summary><ul>%s</ul></details>' % ''.join('<li>%s</li>' % e(s) for s in c['sources'] if s)
    if c['consequence']:
        body += '<p class="c5614-cons"><em>Conséquence :</em> %s</p>' % e(c['consequence'])
    k = c['kind']
    actions = ''
    if k == 'mission5614':
        dk = c['decision_kind']
        p = c.get('payload', {})
        if dk == 'validation':
            actions += '<input type="hidden" data-field="sha256" value="%s"><input data-field="motive" placeholder="Motif si réserve conservée (facultatif)">' % e(str(p.get('sha256', '')), quote=True)
            actions += '<button type="button" class="ax-btn" data-act="m5614-validate">Valider cette version</button>'
        elif dk == 'champs_manquants':
            for f in p.get('fields', [])[:12]:
                actions += '<label class="c5614-field">%s<input data-answer="%s"></label>' % (e(f), e(f, quote=True))
            actions += '<label class="c5614-field">Juridiction<select data-answer="juridiction"><option value="">—</option><option value="tj">Tribunal judiciaire</option><option value="tc">Tribunal de commerce</option><option value="tae">TAE</option><option value="jex">JEX</option></select></label>'
            actions += '<label class="c5614-field">Voie<select data-answer="voie"><option value="">—</option><option value="fond">Au fond</option><option value="refere">Référé</option><option value="execution">Exécution</option></select></label>'
            actions += '<label class="c5614-field">Représentation<select data-answer="representation"><option value="">—</option><option value="avocat">Par avocat</option><option value="sans">Sans représentation obligatoire</option></select></label>'
            actions += '<button type="button" class="ax-btn" data-act="m5614-answer">Valider les réponses</button>'
        elif dk in ('profil_ambigu', 'profil_hors_couverture'):
            from . import profils5614
            opts = p.get('options') or [{'id': pid, 'label': pr['label'], 'consequence': pr['summary']} for pid, pr in profils5614.PROFILES.items()]
            actions += '<select data-answer="profile">' + ''.join('<option value="%s" title="%s">%s</option>' % (e(o['id'], quote=True), e(o.get('consequence', ''), quote=True), e(o['label'])) for o in opts) + '</select>'
            actions += '<button type="button" class="ax-btn" data-act="m5614-answer">Retenir cette procédure</button>'
        elif dk == 'ecritures_ambigues':
            cands = p.get('candidates', [])
            opt = ''.join('<option value="%s">%s (%s, %s)</option>' % (e(x['path'], quote=True), e(x['name']), e(x.get('suggested_side', '')), e(x.get('modified', '')[:10])) for x in cands)
            actions += '<label class="c5614-field">Nos conclusions<select data-answer="ecritures_notres">%s</select></label><label class="c5614-field">Conclusions adverses<select data-answer="ecritures_adverses">%s</select></label>' % (opt, opt)
            actions += '<button type="button" class="ax-btn" data-act="m5614-answer">Valider la sélection</button>'
        elif dk == 'reserve':
            actions += '<button type="button" class="ax-btn" data-act="m5614-reserve-fix">Demander la correction</button>'
        elif dk == 'budget':
            actions += '<input data-field="budget" type="number" min="1" placeholder="Nouveau budget d’appels"><button type="button" class="ax-btn" data-act="m5614-budget">Augmenter et reprendre</button>'
        elif dk == 'role_desactive':
            actions += '<a class="ax-btn" href="%s">Activer le rôle</a>' % e('/parametres/proactivite', quote=True)
        actions += ('<details class="c5614-more"><summary>Modifier / compléter</summary><textarea data-field="instruction" rows="2" placeholder="Consigne complémentaire (relance les seules étapes concernées)"></textarea>'
                    '<input data-field="codes" placeholder="Tâches visées (codes, ex. T12d) — vide : sections et assemblage">'
                    '<button type="button" class="ax-btn ghost" data-act="m5614-revise">Compléter les instructions</button></details>')
        actions += '<button type="button" class="ax-btn ghost" data-act="defer">Reporter</button><button type="button" class="ax-btn ghost" data-act="m5614-cancel" data-confirm="Annuler la mission ? Les fichiers déjà déposés sont conservés.">Annuler</button>'
    elif k == 'mission':
        if c['state'] == 'decision':
            actions += '<select data-field="matter">%s</select><button type="button" class="ax-btn" data-act="mission-resolve">Préciser et démarrer</button>' % _options(labels, c['candidates'], c['matter'])
        else:
            actions += '<button type="button" class="ax-btn" data-act="mission-resume">Relancer</button>'
        actions += ('<details class="c5614-more"><summary>Compléter les instructions</summary><textarea data-field="instruction" rows="2" placeholder="Instruction complémentaire : une mission de suite est créée dans le même dossier"></textarea>'
                    '<button type="button" class="ax-btn ghost" data-act="mission-complete">Envoyer</button></details>')
        actions += '<button type="button" class="ax-btn ghost" data-act="defer">Reporter</button><button type="button" class="ax-btn ghost" data-act="mission-cancel">Annuler</button>'
    elif k == 'docreq':
        actions += '<select data-field="matter">%s</select><button type="button" class="ax-btn" data-act="docreq-resolve">%s</button>' % (
            _options(labels, c['candidates'], c['matter']), 'Choisir le dossier et rédiger' if c['state'] == 'dossier_a_choisir' else 'Relancer')
        actions += '<button type="button" class="ax-btn ghost" data-act="defer">Reporter</button>'
    elif k == 'document':
        actions += ('<select data-field="matter">%s</select><input data-field="created" placeholder="Date de création prouvée (ex. 2026-10-06T10:00:00+02:00)"><input data-field="proof" placeholder="Preuve ou explication">'
                    '<button type="button" class="ax-btn" data-act="resolve">Confirmer et reprendre</button><button type="button" class="ax-btn ghost" data-act="retry">Relancer</button>'
                    '<button type="button" class="ax-btn ghost" data-act="defer">Reporter</button><button type="button" class="ax-btn ghost" data-act="dismiss">Écarter</button>') % _options(labels, c['candidates'], c['matter'])
    elif k == 'plan':
        actions += '<button type="button" class="ax-btn" data-act="plan-resume">Reprendre la suite</button><button type="button" class="ax-btn ghost" data-act="defer">Reporter</button>'
    elif k == 'mails':
        actions += '<a class="ax-btn" href="%s">Rattacher</a><button type="button" class="ax-btn ghost" data-act="defer">Reporter</button>' % e(c['url'], quote=True)
    elif k == 'rule':
        actions += ('<button type="button" class="ax-btn" data-act="rule-adopt">Adopter pour le cabinet</button>' + ('<button type="button" class="ax-btn ghost" data-act="rule-adopt-matter">Ce dossier seulement</button>' if c['matter'] else '')
                    + '<button type="button" class="ax-btn ghost" data-act="rule-ignore">Ignorer</button>')
    elif k == 'commitment':
        actions += '<a class="ax-btn" href="%s">Décider dans Engagements</a><button type="button" class="ax-btn ghost" data-act="defer">Reporter</button>' % e(c['url'], quote=True)
    detail = '<a class="ax-btn ghost" href="%s">Détail</a>' % e(c['url'], quote=True) if c.get('url') else ''
    return '<article class="c5614-card" data-key="%s" data-kind="%s" data-id="%s" data-mission="%s" tabindex="-1">%s%s<div class="c5614-actions">%s%s</div></article>' % (
        e(c['key'], quote=True), e(k), e(str(c['id']), quote=True), e(str(c.get('mission', '')), quote=True), head, body, actions, detail)


def decisions_html(desk, owner='cabinet', prefix=''):
    rows = items(desk, owner, prefix)
    if not rows:
        return ''
    urgent = sum(1 for c in rows if c['urgent'])
    labels = {str(m['id']): matter_display(m) for m in load_matters(desk.c)}
    cards = ''.join(card_html(desk, c, labels) for c in rows)
    return ('<section class="c530-card c5614-decisions" id="c569-decisions" aria-labelledby="c5614-title">'
            '<div class="c5614-summary"><h2 id="c5614-title">À décider <span class="c530-badge">%d</span>%s</h2>'
            '<p class="c530-sub">%s</p>'
            '<button type="button" class="ax-btn%s" id="c5614-open" aria-controls="c5614-panel" aria-expanded="false"%s>%s</button></div>'
            '<aside id="c5614-panel" class="c5614-panel" hidden aria-label="Décisions attendues"><header><h3>Décisions attendues</h3>'
            '<button type="button" class="c530-icon" id="c5614-close" aria-label="Replier">×</button></header><div class="c5614-list">%s</div>'
            '<p id="c569-status" role="status"></p></aside></section>') % (
        len(rows), (' <span class="c5614-urgent">%d urgente(s)</span>' % urgent) if urgent else '',
        'L’agent s’est arrêté là où votre décision est nécessaire. Un dossier suggéré n’est jamais choisi à votre place.' if rows else 'Aucune décision en attente.',
        '' if rows else ' ghost', ' disabled' if not rows else '', 'Ouvrir les décisions' if rows else 'Rien à décider',
        cards if rows else '<p class="c530-empty">Aucune décision en attente.</p>')
