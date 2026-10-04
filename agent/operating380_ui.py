"""HTML views for the AxiorHub 3.8 operating layer."""
from html import escape
import json
from urllib.parse import parse_qsl, urlsplit

from .improvements36 import matter_option
from .operating380 import (BUCKETS, CAPABILITIES, action_center, evaluation_runs,
                           matter_graph, playbook_run, playbooks,
                           recent_playbook_runs, relevance_metrics, services)


def e(value):return escape(str(value if value is not None else ''),quote=True)


def today_page(desk, form, link):
    data=action_center(desk);labels={'urgent':'Urgent','ready':'Prêt à utiliser','decision':'Décision requise','done':'Réalisé'}
    out='<section class="op-hero"><p class="eyebrow">VERSION 3.9.0 · CABINET OPÉRANT</p><h1>Aujourd’hui</h1><p>AxiorHub rassemble ce qui exige votre attention, ce qui est déjà préparé et ce qui a effectivement été réalisé.</p></section>'
    out+='<div class="op-summary">'+''.join('<a href="#op-'+k+'"><strong>'+str(data['counts'][k])+'</strong><span>'+e(labels[k])+'</span></a>' for k in BUCKETS)+'</div>'
    out+='<div class="op-board">'
    for bucket in BUCKETS:
        out+='<section class="op-column" id="op-'+bucket+'"><h2>'+e(labels[bucket])+' <span>'+str(data['counts'][bucket])+'</span></h2>'
        cards=[x for x in data['cards'] if x['bucket']==bucket]
        if not cards:out+='<p class="empty">Aucune action dans cette rubrique.</p>'
        for card in cards:
            out+='<article class="op-card op-risk-'+e(card['risk'])+'"><small>'+e(card['matter_name'])+' · confiance '+str(card['confidence'])+' %</small><h3>'+e(card['title'])+'</h3><p>'+e(card['reason'])+'</p>'
            if card['source_ids']:out+='<details><summary>Sources et déclencheurs</summary><ul>'+''.join('<li>'+e(x)+'</li>' for x in card['source_ids'])+'</ul></details>'
            target=urlsplit(card['href'])
            # All application links must go through the shared helper so the
            # deployment prefix (normally /agent-courriel) is preserved.
            action_link=link(target.path,card['action_label'],**dict(parse_qsl(target.query,keep_blank_values=True)))
            out+='<div class="op-card-actions">'+action_link
            if bucket!='done':out+=form('review_action380','Écarter',{'action_id':card['id'],'state':'dismissed'})
            else:out+=form('review_action380','Marquer comme vu',{'action_id':card['id'],'state':'seen'})
            out+='</div></article>'
        out+='</section>'
    return out+'</div><p class="notice">'+e(data['warning'])+'</p>'


def playbooks_page(desk, matters, args, form, link):
    selected=args.get('matter','');run_id=args.get('run','')
    options='<option value="">Choisir le dossier complet</option>'+''.join('<option value="'+e(m['id'])+'"'+(' selected' if m['id']==selected else '')+'>'+e(matter_option(m))+'</option>' for m in matters)
    out='<section class="op-hero"><p class="eyebrow">PROCÉDURES MÉTIER</p><h1>Playbooks</h1><p>Chaque parcours réutilise les tâches, contrôles, documents et recherches existants. Aucune étape engageante n’est exécutée sans validation.</p></section>'
    out+='<div class="op-playbooks">'
    for item in playbooks(desk):
        steps='<ol>'+''.join('<li>'+e(s['label'])+(' · validation' if s.get('manual') else '')+'</li>' for s in item['steps'])+'</ol>'
        extra='<label>Dossier<select required name="matter">'+options+'</select></label><label>Objectif ou consigne<textarea name="objective" maxlength="3000" rows="3"></textarea></label>'
        out+='<article><small>'+e(item['family'])+'</small><h2>'+e(item['name'])+'</h2><p>'+e(item['description'])+'</p><details><summary>Étapes</summary>'+steps+'</details>'+form('start_playbook380','Démarrer ce parcours',{'playbook_id':item['id']},extra)+'</article>'
    out+='</div><h2>Parcours récents</h2>'
    for run in recent_playbook_runs(desk,selected,30):
        out+='<article class="op-run"><div><small>'+e(run['status'])+'</small><h3>'+e(run['playbook_id'])+' · '+e(run['matter'])+'</h3></div><a href="?run='+e(run['id'])+'&matter='+e(run['matter'])+'">Voir le parcours</a></article>'
    if run_id:
        run=playbook_run(desk,run_id);out+='<section class="op-run-detail"><h2>Parcours '+e(run['playbook_id'])+'</h2><p>Dossier : '+link('/matter',run['matter'],id=run['matter'])+' · état : <strong>'+e(run['status'])+'</strong></p><ol>'
        for step in run['steps']:
            out+='<li class="op-step-'+e(step['status'])+'"><strong>'+e(step['label'])+'</strong> · '+e(step['status'])
            if step['result'].get('target'):out+=' · '+link(step['result']['target'],'Ouvrir l’étape',matter=run['matter'])
            if step['status']=='awaiting_input':out+=form('complete_playbook_step380','Marquer l’étape comme contrôlée',{'run_id':run['id'],'step_no':step['step_no']})
            out+='</li>'
        out+='</ol>'
        if run['status'] in ('running','awaiting_input'):out+=form('advance_playbook380','Continuer le parcours',{'run_id':run['id']})
        out+='</section>'
    return out


def ecosystem_page(desk, matters, form):
    out='<section class="op-hero"><p class="eyebrow">API AXIORHUB</p><h1>Écosystème du cabinet</h1><p>Les services spécialisés deviennent des capacités contrôlées. Le noyau prépare des enveloppes auditables ; seuls les connecteurs autorisés les transmettent.</p></section>'
    caps=', '.join(CAPABILITIES)
    out+='<section><h2>Enregistrer un service</h2>'+form('save_ecosystem_service380','Enregistrer le service',extra='<label>Identifiant<input required name="service_id" pattern="[a-z][a-z0-9_-]{1,63}" maxlength="64" placeholder="lexdelai"></label><label>Nom<input required name="name" maxlength="120"></label><label>URL API HTTPS<input required type="url" name="base_url" maxlength="1000" placeholder="https://service.example.com/api"></label><label>Capacités séparées par des virgules<input required name="capabilities" value="calculate_deadline" maxlength="1000"><small>'+e(caps)+'</small></label><label>Référence du secret dans le coffre<input name="secret_ref" maxlength="120" placeholder="ecosystem:lexdelai"></label><label><input type="checkbox" name="reviewed" value="yes"> Configuration relue</label><label><input type="checkbox" name="enabled" value="yes"> Activer</label>')+'</section>'
    out+='<section><h2>Services configurés</h2><div class="op-services">'
    rows=services(desk)
    if not rows:out+='<p class="empty">Aucun service enregistré.</p>'
    for item in rows:
        out+='<article><small>'+('Actif' if item['enabled'] else 'Inactif')+' · '+('secret configuré' if item['secret_configured'] else 'sans secret')+'</small><h3>'+e(item['name'])+'</h3><p>'+e(item['base_url'])+'</p><p>'+e(', '.join(CAPABILITIES.get(x,x) for x in item['capabilities']))+'</p></article>'
    return out+'</div></section>'


def evaluation_page(desk, form):
    metrics=relevance_metrics(desk);runs=evaluation_runs(desk,10)
    labels={'job_reliability_pct':'Fiabilité des opérations','independent_control_approval_pct':'Contrôles indépendants favorables','wrong_matter_feedback':'Retours mauvais dossier','drafts_ready':'Brouillons prêts','handled_items':'Éléments traités','confirmed_matter_links':'Associations confirmées','automatic_matter_links':'Associations automatiques','accepted_corrections':'Corrections apprises','graph_source_coverage_pct':'Couverture sourcée du graphe'}
    out='<section class="op-hero"><p class="eyebrow">PERTINENCE MESURABLE</p><h1>Banc d’évaluation métier</h1><p>Les résultats sont accompagnés de leurs dénominateurs. Un bon score technique ne constitue pas une validation juridique.</p>'+form('run_business_evaluation380','Exécuter le banc d’évaluation')+'</section><div class="op-metrics">'
    for key,label in labels.items():
        value=metrics.get(key);suffix=' %' if key.endswith('_pct') and value is not None else ''
        out+='<article><strong>'+e('Non mesuré' if value is None else value)+suffix+'</strong><span>'+e(label)+'</span></article>'
    out+='</div><p class="notice">'+e(metrics['limits'])+'</p><h2>Exécutions récentes</h2>'
    if not runs:out+='<p class="empty">Le banc n’a pas encore été exécuté.</p>'
    for run in runs:
        out+='<article class="op-eval"><h3>'+str(run['score'])+' %</h3><ul>'+''.join('<li>'+('✓' if x['passed'] else '✗')+' '+e(x['case'])+' — '+e(x['detail'])+'</li>' for x in run['results'])+'</ul><small>'+e(run['created'])+'</small></article>'
    return out


def matter_graph_section(desk, matter, form, link):
    graph=matter_graph(desk,matter['id']);nodes={n['id']:n for n in graph['nodes']}
    out='<section id="ws-matter-arguments"><div class="section-heading"><div><h2>Arguments et preuves</h2><p>Faits, prétentions, arguments, preuves, contradictions et éléments manquants reliés à leurs sources.</p></div>'+form('refresh_matter_graph380','Actualiser le graphe',{'matter':matter['id']})+'</div>'
    out+='<div class="op-graph-counts">'+''.join('<span><strong>'+str(value)+'</strong> '+e(key)+'</span>' for key,value in graph['counts'].items())+'</div>'
    if not graph['nodes']:out+='<p class="empty">Le graphe n’est pas encore construit. Actualisez d’abord la mémoire ou la matrice du dossier.</p>'
    else:
        out+='<div class="table"><table><thead><tr><th>Type</th><th>Élément</th><th>État</th><th>Sources</th></tr></thead><tbody>'
        for n in graph['nodes'][:120]:out+='<tr><td>'+e(n['node_kind'])+'</td><td><strong>'+e(n['title'])+'</strong><small>'+e(n['detail'][:300])+'</small></td><td>'+e(n['status'])+'</td><td>'+e(', '.join(n['source_ids'][:4]) or 'À compléter')+'</td></tr>'
        out+='</tbody></table></div><details><summary>Relations du graphe</summary><ul>'
        for edge in graph['edges'][:160]:out+='<li>'+e(nodes.get(edge['from_id'],{}).get('title',edge['from_id'])[:90])+' <strong>'+e(edge['relation'])+'</strong> '+e(nodes.get(edge['to_id'],{}).get('title',edge['to_id'])[:90])+'</li>'
        out+='</ul></details>'
    return out+'<p class="notice">'+e(graph['warning'])+'</p></section>'


def matter_fees_section(desk, matter, link):
    mid=matter['id'];billing=desk.db.execute("SELECT COUNT(*),COALESCE(SUM(estimated_minutes),0) FROM billing_proposals_v230 WHERE matter=? AND status='pending'",(mid,)).fetchone()
    provisions=desk.db.execute('SELECT COUNT(*),COALESCE(SUM(requested_cents),0) FROM cabinet_provisions_v290 WHERE matter=?',(mid,)).fetchone()
    pilotage=link('/pilotage','Ouvrir le pilotage et la facturation')
    return '<section id="ws-matter-fees"><h2>Honoraires</h2><div class="op-summary"><article><strong>'+str(billing[0])+'</strong><span>Diligences à facturer · '+str(billing[1])+' min à confirmer</span></article><article><strong>'+str(round(provisions[1]/100,2))+' €</strong><span>Provisions enregistrées · '+str(provisions[0])+'</span></article></div><p>'+pilotage+'</p></section>'
