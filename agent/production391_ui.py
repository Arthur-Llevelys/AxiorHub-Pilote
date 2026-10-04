"""Compatibility production views used by AxiorHub 3.9.2."""
from html import escape
import json

from .common import load_matters
from .improvements36 import matter_label
from .operating380 import action_center
from .production391 import dashboard


def e(value):return escape(str(value if value is not None else ''),quote=True)


def _matter_names(desk):
    return {m['id']:matter_label(m) for m in load_matters(desk.c)}


def _flow_card(item, names, form, link, incident=False):
    name=names.get(item['matter'],item['matter'] or 'Cabinet')
    out='<article class="flow-card flow-'+e(item['status'])+'"><div class="flow-card-head"><div><small>'+e(name)+' · '+e(item['updated'])+'</small><h3>'+e(item['job_kind'].replace('_',' '))+'</h3></div><span>'+e(item['status'].replace('_',' '))+'</span></div>'
    out+='<ol class="flow-stages">'+''.join('<li class="stage-'+e(x['status'])+'"><span></span>'+e(x['label'])+'</li>' for x in item['stages'])+'</ol>'
    out+='<dl class="flow-meta"><div><dt>Source</dt><dd>'+e(item['source_id'])+'</dd></div><div><dt>Modèle</dt><dd>'+e(item['model_provider']+' · '+item['model_name'])+'</dd></div></dl>'
    if item['paths']:out+='<details><summary>Emplacements produits</summary><ul>'+''.join('<li>'+e(x)+'</li>' for x in item['paths'])+'</ul></details>'
    if item['last_error']:out+='<p class="flow-error"><strong>Cause :</strong> '+e(item['last_error'])+'</p>'
    out+='<div class="flow-actions">'
    if item['matter']:out+=link('/matter','Ouvrir le dossier',id=item['matter'])
    if item.get('output_id'):out+=link('/production','Relire le livrable',output=item['output_id'])
    if incident:out+=form('retry_production391','Relancer depuis l’incident',{'job_id':item['job_id']})
    return out+'</div></article>'


def today_page(desk, form, link):
    center=action_center(desk);prod=dashboard(desk);names=_matter_names(desk)
    urgent=[x for x in center['cards'] if x['bucket']=='urgent'][:12]
    decisions=[x for x in center['cards'] if x['bucket']=='decision'][:12]
    prepared=([x for x in center['cards'] if x['bucket']=='ready']+
              [x for x in prod['flows'] if x['status'] in ('prepared','delivered','verified')])[:12]
    incidents=prod['incidents'][:12]
    done=[x for x in prod['flows'] if x['status']=='verified'][:8]
    buckets=[('now','À faire maintenant',urgent),('prepared','Préparé par AxiorHub',prepared),
      ('decision','Décision nécessaire',decisions),('incidents','Incidents',incidents),('done','Terminé récemment',done)]
    out='<section class="today391-hero"><div><p class="eyebrow">VERSION 3.9.2 · EXPÉRIENCE UTILISATEUR</p><h1>Aujourd’hui</h1><p>Les éléments sont classés selon la décision réellement attendue, pas selon les opérations techniques.</p></div>'+form('production_cycle391','Produire maintenant',{'limit':'30'})+'</section>'
    out+='<nav class="today391-tabs" aria-label="Rubriques du jour">'+''.join('<a href="#today-'+key+'"><strong>'+str(len(rows))+'</strong>'+e(label)+'</a>' for key,label,rows in buckets)+'</nav>'
    for key,label,rows in buckets:
        out+='<section class="today391-section" id="today-'+key+'"><div class="section-heading"><h2>'+e(label)+'</h2><span>'+str(len(rows))+'</span></div><div class="today391-grid">'
        if not rows:out+='<p class="empty">Rien dans cette rubrique.</p>'
        else:
            for card in rows:
                if 'bucket' not in card:
                    out+=_flow_card(card,names,form,link,key=='incidents');continue
                out+='<article class="today-action"><small>'+e(card['matter_name'])+' · confiance '+str(card['confidence'])+' %</small><h3>'+e(card['title'])+'</h3><p>'+e(card['reason'])+'</p><div>'
                href=str(card.get('href') or '')
                if href.startswith('/'):
                    from urllib.parse import parse_qsl,urlsplit
                    target=urlsplit(href);out+=link(target.path,card['action_label'],**dict(parse_qsl(target.query,keep_blank_values=True)))
                else:out+=link('/production','Examiner dans Production')
                out+='</div></article>'
        out+='</div></section>'
    return out


def _review_panel(desk, output_id, form, link):
    row=desk.db.execute('SELECT * FROM production_outputs_v390 WHERE id=?',(str(output_id or ''),)).fetchone()
    if not row:return '<p class="notice">Le livrable demandé n’existe plus.</p>'
    paths=json.loads(row['paths'] or '[]');detail=json.loads(row['detail'] or '{}')
    out='<section class="review391"><div class="review391-head"><div><p class="eyebrow">RELECTURE DU LIVRABLE</p><h2>'+e(row['label'])+'</h2><p>'+e(row['matter'] or 'Cabinet')+' · '+e(row['updated'])+'</p></div>'+link('/production','Fermer la relecture')+'</div><div class="review391-workspace"><article><h3>Document et versions</h3>'
    if paths:out+='<ul class="review391-paths">'+''.join('<li>'+e(x)+'</li>' for x in paths)+'</ul>'
    else:out+='<p class="empty">Livrable conservé dans la messagerie ou dans le registre interne.</p>'
    out+='<pre>'+e(detail.get('message') or 'Le contenu complet reste dans son fichier ou dans INBOX.Drafts afin de ne pas le recopier dans le journal technique.')+'</pre>'
    if row['matter']:out+='<p>'+link('/matter','Ouvrir le dossier, OnlyOffice et les versions',id=row['matter'])+'</p>'
    out+='</article><aside><h3>Sources et contrôle</h3><dl><dt>Source</dt><dd>'+e(row['source_id'])+'</dd><dt>Traitement</dt><dd>'+e(row['job_kind'])+'</dd><dt>État</dt><dd>'+e(row['status'])+'</dd></dl><p>Vérifiez les références dans le document original avant tout engagement externe.</p></aside></div>'
    out+='<div class="review391-decisions">'+form('review_output391','Accepter',{'output_id':row['id'],'decision':'accepted'},'<label>Note facultative<textarea name="note" rows="2" maxlength="3000"></textarea></label>')
    out+=form('review_output391','Modifier et apprendre',{'output_id':row['id'],'decision':'modified'},'<label>Version corrigée ou extrait corrigé<textarea required name="corrected" rows="5" maxlength="12000"></textarea></label><label>Règle explicite à retenir<textarea required name="guidance" rows="3" maxlength="1500" placeholder="Ex. Toujours rappeler la référence du dossier dans l’objet."></textarea></label>')
    out+=form('review_output391','Rejeter et expliquer',{'output_id':row['id'],'decision':'rejected'},'<label>Motif du rejet<textarea required name="note" rows="3" maxlength="3000"></textarea></label>')+'</div></section>'
    return out


def page(desk, args, form, link):
    data=dashboard(desk);c=data['counts'];names=_matter_names(desk)
    if args.get('output'):return _review_panel(desk,args['output'],form,link)
    out='<section class="prod391-hero"><div><p class="eyebrow">VERSION 3.9.2 · EXPÉRIENCE UTILISATEUR · Production réelle</p><h1>Chaîne de production</h1><p>Chaque travail montre son origine, son dossier, le modèle utilisé, ses contrôles, son emplacement et la seule décision qui reste à prendre.</p></div>'+form('production_cycle391','Lancer la production',{'limit':'30'})+'</section>'
    metrics=((c['verified_mail_drafts'],'Brouillons IMAP vérifiés'),(str(data['mail_draft_coverage_percent'])+' %','Couverture courriels'),(c['prepared_projects'],'Projets préparés'),(c['delivered_outputs'],'Livrables disponibles'),(len(data['incidents']),'Incidents à corriger'))
    out+='<section class="prod391-kpis">'+''.join('<article><strong>'+e(v)+'</strong><span>'+e(l)+'</span></article>' for v,l in metrics)+'</section>'
    if data['incidents']:
        out+='<section class="prod391-incidents"><div class="section-heading"><div><h2>À corriger</h2><p>Cause explicite, étape atteinte et reprise bornée à trois tentatives.</p></div></div><div class="flow-grid">'+''.join(_flow_card(x,names,form,link,True) for x in data['incidents'])+'</div></section>'
    out+='<section class="card"><div class="section-heading"><div><h2>Travaux récents</h2><p>La frise permet de voir immédiatement où chaque production s’est arrêtée.</p></div>'+form('advance_playbooks391','Continuer les playbooks',{'limit':'30'})+'</div><div class="flow-grid">'
    if not data['flows']:out+='<p class="empty">Aucun flux 3.9.2 enregistré. Lancez la production ou confiez une instruction à l’assistant transversal.</p>'
    else:out+=''.join(_flow_card(x,names,form,link) for x in data['flows'])
    out+='</div></section><section class="prod391-review"><h2>Validation utile, pas validation permanente</h2><div><strong>AxiorHub exécute</strong><p>Lecture, indexation, rapprochement, brouillons IMAP, projets de conclusions, notes de plaidoirie et fichiers internes nouveaux.</p></div><div><strong>L’avocat décide</strong><p>Envoi, signature, dépôt RPVA, paiement, suppression et écrasement d’une source.</p></div></section>'
    return out
