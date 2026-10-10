"""5.6.14 : vue de mission complexe (arbre de tâches, état résumé, prochaine action, filtres) et page des profils procéduraux."""
from html import escape as e
import json

from .common import Stop, load_matters, matter_display
from . import taches5614, parcours5614


def _task_html(node):
    cls = node['outcome'] if node['state'] == 'terminee' else node['state']
    badge = '<span class="m5614-state %s">%s</span>' % (e(cls), e(node['label'] if node['state'] != 'terminee' else node['outcome_label']))
    waiting = (' <small>attend %s</small>' % e(', '.join(node['waiting_for']))) if node['waiting_for'] and node['state'] in ('a_preparer', 'en_attente') else ''
    err = (' <small class="warn">%s</small>' % e(node['error'])) if node['error'] else ''
    req = '' if node['required'] else ' <small>(facultatif)</small>'
    children = ('<ul>%s</ul>' % ''.join(_task_html(c) for c in node['children'])) if node['children'] else ''
    return ('<li><div class="m5614-task" data-state="%s" data-outcome="%s"%s><span class="m5614-code">%s</span><strong>%s</strong><small>%s</small>%s%s%s%s</div>%s</li>' % (
        e(node['state']), e(node['outcome']), ' data-has-result="1"' if node['has_result'] else '', e(node['code']), e(node['title']), e(node['role']), req, badge, waiting, err, children))


def mission_html(m, prefix, detailed=True):
    pct = int(100 * m['progress']['accepted'] / m['progress']['required']) if m['progress']['required'] else 0
    head = ('<header><h2>%s</h2><p class="c530-sub">%s · %s · révision du plan %d · appels %d/%d%s</p>'
            '<div class="m5614-progress" aria-label="Livrables acceptés"><span style="width:%d%%"></span></div><p class="m5614-next">Prochaine action : %s</p></header>') % (
        e(m['title']), e(m['label']), e(m['matter_label']), m['plan_revision'], m['budget']['calls_used'], m['budget']['budget_calls'],
        (' · échéance ' + e(m['deadline'])) if m['deadline'] else '', pct, e(m['next_action']))
    if not detailed:
        return '<article class="m5614-mission"><a href="%s">%s</a>%s</article>' % (e(m['url'], quote=True), e(m['title']), head)
    actions = '<div class="c5614-actions">'
    if m['state'] in ('active', 'decision', 'bloquee'):
        actions += '<button type="button" class="ax-btn ghost" data-m5614="pause" data-id="%s">Suspendre</button>' % e(m['id'], quote=True)
    if m['state'] in ('suspendue', 'bloquee', 'suggested'):
        actions += '<button type="button" class="ax-btn" data-m5614="resume" data-id="%s">%s</button>' % (e(m['id'], quote=True), 'Lancer la préparation' if m['state'] == 'suggested' else 'Reprendre')
    if m['state'] not in ('annulee', 'validee'):
        actions += '<button type="button" class="ax-btn ghost" data-m5614="cancel" data-id="%s" data-confirm="Annuler la mission ? Les fichiers déposés sont conservés.">Annuler</button>' % e(m['id'], quote=True)
    actions += '<button type="button" class="ax-btn ghost" data-m5614="run" data-id="%s" title="Exécuter maintenant les tâches en attente (sans attendre le worker)">Exécuter maintenant</button>' % e(m['id'], quote=True)
    actions += '</div>'
    revise = ('<details class="c5614-more"><summary>Modifier le plan / compléter les instructions</summary><textarea id="m5614-instruction" rows="2" placeholder="Ex. Reprends uniquement la discussion sur la prescription"></textarea>'
              '<input id="m5614-codes" placeholder="Codes de tâches visés (ex. T12d) — vide : sections, assemblage et présentation">'
              '<button type="button" class="ax-btn ghost" data-m5614="revise" data-id="%s">Appliquer (nouvelle révision du plan)</button></details>') % e(m['id'], quote=True)
    decisions = ''
    for d in m['decisions']:
        decisions += '<li><strong>%s</strong> — %s</li>' % (e(d['label']), e(d['question']))
    deliverable = ''
    if m['deliverable']:
        final = next((t for t in m['tasks'] if t['task_type'] == 'assemblage' and t['result']), None)
        content = final['result']['content'] if final else {}
        ctrl = content.get('control', {})
        deliverable = ('<section class="ax-card"><h3>Projet</h3><p><a href="%s">%s</a> · modèle : %s</p><p>%s</p>%s' % (
            e(prefix + '/documents/edit?path=' + content.get('path', ''), quote=True), e(content.get('path', '').rsplit('/', 1)[-1]), e(content.get('template', {}).get('label', '')),
            e(ctrl.get('summary', '')),
            ('<details><summary>%d défaut(s) et réserves</summary><ul>%s</ul></details>' % (len(ctrl.get('defects', [])), ''.join('<li>%s — %s</li>' % (e(x.get('localisation', '')), e(x.get('message', x.get('regle_ou_source', '')))) for x in ctrl.get('defects', [])[:30]) + ''.join('<li>%s</li>' % e(r) for r in ctrl.get('reserves', [])))) if ctrl else ''))
        if m['validation'] == 'a_decider':
            deliverable += ('<p><input id="m5614-motive" placeholder="Motif si réserve conservée"> <button type="button" class="ax-btn" data-m5614="validate" data-id="%s" data-sha="%s">Valider cette version (%s)</button></p>' % (
                e(m['id'], quote=True), e(content.get('sha256', ''), quote=True), e(content.get('sha256', '')[:12])))
        elif m['validation'] == 'validee':
            deliverable += '<p class="ok">Version validée (hash %s).</p>' % e(m['validated_hash'][:12])
        deliverable += '</section>'
    filters = ('<div class="m5614-filters"><button type="button" class="ax-btn ghost" data-m5614-filter="tout" aria-pressed="true">Tout</button>'
               '<button type="button" class="ax-btn ghost" data-m5614-filter="blocages" aria-pressed="false">Blocages</button>'
               '<button type="button" class="ax-btn ghost" data-m5614-filter="resultats" aria-pressed="false">Résultats à relire</button></div>')
    tree = '<ul class="m5614-tree">%s</ul>' % ''.join(_task_html(n) for n in m['tree'])
    return ('<article class="m5614-mission" id="m5614-%s">%s%s%s%s%s%s<p id="m5614-status" role="status"></p></article>' % (
        e(m['id'], quote=True), head, ('<section class="ax-card"><h3>Décisions attendues</h3><ul>%s</ul><p><a href="%s/aujourdhui">Décider dans « À décider »</a></p></section>' % (decisions, e(prefix))) if decisions else '',
        deliverable, actions, revise, filters + tree))


def page(desk, auth, prefix, env, args):
    from .web440 import shell
    from .web567 import actor
    owner, role = actor(env)
    ident = str(args.get('id') or '')
    body = '<div class="m5614-page" id="m5614-page"><h1>Missions complexes</h1><p>Une mission principale, ses sous-tâches par rôle, ses décisions et son projet final. Les résultats parents sont transmis intégralement ; une abstention reste visible et bloque.</p>'
    labels = sorted(((m['id'], matter_display(m)) for m in load_matters(desk.c)), key=lambda x: x[1].casefold())
    body += ('<details class="ax-card"><summary>Lancer une mission (assignation ou conclusions)</summary><form class="m5-form" data-api="m568/mission5614/create" data-reload="1">'
             '<label class="m5-field">Dossier<select name="matter" required>%s</select></label>'
             '<label class="m5-field">Parcours<select name="parcours"><option value="assignation">Assignation</option><option value="conclusions">Conclusions en réponse</option></select></label>'
             '<label class="m5-field">Instruction<textarea name="instruction" rows="3" required placeholder="Ex. Prépare une assignation au fond devant le tribunal judiciaire pour recouvrer les loyers impayés"></textarea></label>'
             '<label class="m5-field">Juridiction<select name="juridiction"><option value="">à déterminer</option><option value="tj">Tribunal judiciaire</option><option value="tc">Tribunal de commerce</option><option value="tae">TAE</option><option value="jex">JEX</option></select></label>'
             '<label class="m5-field">Voie<select name="voie"><option value="">à déterminer</option><option value="fond">Au fond</option><option value="refere">Référé</option><option value="execution">Exécution</option></select></label>'
             '<label class="m5-field">Représentation<select name="representation"><option value="">à déterminer</option><option value="avocat">Par avocat</option><option value="sans">Sans représentation obligatoire</option></select></label>'
             '<label class="m5-field">Autonomie<select name="autonomy"><option value="prepare">Préparer automatiquement</option><option value="suggest">Proposer le plan seulement</option></select></label>'
             '<input type="hidden" name="request_key" value="%s"><button class="ax-btn" type="submit">Créer la mission</button></form></details>') % (
        ''.join('<option value="%s">%s</option>' % (e(k, quote=True), e(v)) for k, v in labels), e(__import__('secrets').token_hex(12), quote=True))
    if ident:
        try:
            m = taches5614.get(desk, ident, owner, role == 'administrateur', prefix)
            body += mission_html(m, prefix)
        except Stop as ex:
            body += '<p class="notice">%s</p>' % e(str(ex))
    rows = taches5614.listing(desk, owner, role == 'administrateur', prefix)
    body += '<h2>Missions</h2>' + (''.join(mission_html(m, prefix, detailed=False) for m in rows) if rows else '<p class="c530-empty">Aucune mission complexe.</p>')
    body += '</div>'   # 5.6.24 (F36) : v5614.js est déjà chargé par l'en-tête commun ; un second chargement doublait chaque commande
    return shell('Missions complexes', body, prefix, auth['csrf'], '/production')


def profils_page(desk, auth, prefix, env):
    from .web440 import shell
    from . import profils5614
    body = '<h1>Profils procéduraux</h1>' + profils5614.section_html(desk, prefix)
    return shell('Profils procéduraux', body, prefix, auth['csrf'], '/parametres')
