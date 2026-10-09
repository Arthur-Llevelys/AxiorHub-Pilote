"""Lawyer-facing result views for AxiorHub 4.2."""
from html import escape
import json
import re
from urllib.parse import parse_qsl, urlsplit

from .common import load_matters
from .improvements36 import matter_label, matter_option
from .operating380 import action_center
from .production420 import STUDIO_TYPES, dashboard, estimate_studio


def e(value): return escape(str(value if value is not None else ''), quote=True)


def _href(link, raw, label):
    target=urlsplit(str(raw or ''))
    return link(target.path,label,**dict(parse_qsl(target.query,keep_blank_values=True))) if target.path.startswith('/') else ''


def _source_details(source_ids, trigger=''):
    rows=list(source_ids or [])[:8]
    out='<details><summary>Sources et déclencheur</summary>'
    if trigger:out+='<p><strong>Déclencheur :</strong> '+e(trigger)+'</p>'
    out+=('<ul>'+''.join('<li>'+e(x)+'</li>' for x in rows)+'</ul>' if rows else '<p>Aucune source technique affichable.</p>')
    return out+'</details>'


def _deliverable_card(item, form, link, incident=False):
    verification=item.get('verification') or {};files=verification.get('files') or []
    out='<article data-live-card="deliverable-'+e(item['id'])+'" class="result420-card result420-'+e(item['status'])+'"><header><div><small>'+e(item['matter_name'])+' · '+e(item['updated'])+'</small><h3>'+e(item['label'])+'</h3></div><span>'+e({'verified':'Disponible','prepared':'À décider','abstained':'Abstention','error':'Incident','verifying':'Vérification'}.get(item['status'],item['status']))+'</span></header>'
    out+='<p>'+e(item['business_message'])+'</p>'+_source_details(item['source_ids'],item['trigger_text'])
    if files:out+='<ul class="result420-files">'+''.join('<li>'+e(x.get('path',''))+'</li>' for x in files)+'</ul>'
    if item.get('stages'):
        out+='<ol class="result420-progress">'+''.join('<li class="is-'+e(x.get('status','pending'))+'">'+e(x.get('label',''))+'</li>' for x in item['stages'])+'</ol>'
    out+='<div class="actions">'
    if item['matter']:out+=link('/matter','Ouvrir le dossier',id=item['matter'])
    if item['deliverable_kind']=='mail_draft':
        if re.fullmatch('[a-f0-9]{64}',str(item.get('source_id',''))):out+=link('/mail','Ouvrir le brouillon',key=item['source_id'])
        else:out+=link('/','Ouvrir les brouillons',view='drafts')
    if item['status'] in ('verified','prepared') and item.get('output_id'):
        out+=link('/production','Relire ou corriger',output=item['output_id'])
    if incident:out+=form('retry_deliverable420','Relancer',{'deliverable_id':item['id']})
    return out+'</div></article>'


def _action_card(card, form, link):
    due=(' · échéance '+e(card.get('due'))) if card.get('due') else ''
    out='<article data-live-card="action-'+e(card.get('id') or card.get('href') or card.get('title'))+'" class="result420-card"><header><div><small>'+e(card.get('matter_name') or 'Cabinet')+due+'</small><h3>'+e(card.get('title'))+'</h3></div></header><p>'+e(card.get('reason'))+'</p>'+_source_details(card.get('source_ids',[]),'Action détectée par AxiorHub')+'<div class="actions">'
    out+=_href(link,card.get('href'),card.get('action_label') or 'Ouvrir') or link('/production','Examiner')
    out+=form('review_action380','Écarter',{'action_id':card.get('id',''),'state':'dismissed'})
    return out+'</div></article>'


def _zone(key,title,subtitle,items,renderer):
    out='<section id="today420-'+key+'" class="today420-zone"><header><div><h2>'+e(title)+'</h2><p>'+e(subtitle)+'</p></div><strong>'+str(len(items))+'</strong></header><div class="today420-grid">'
    out+=''.join(renderer(x) for x in items) if items else '<p class="empty">Rien dans cette rubrique.</p>'
    return out+'</div></section>'


def today_page(desk, form, link):
    center=action_center(desk);data=dashboard(desk)
    from .live430 import snapshot
    represented={x['job_id'] for x in data['incidents']}
    live_incidents=[x for x in snapshot(desk)['jobs'] if (x['status']=='error' or x.get('blocked')) and x['id'] not in represented][:30]
    todo=[x for x in center['cards'] if x['bucket']=='urgent'][:30]
    decisions=[x for x in center['cards'] if x['bucket']=='decision'][:30]+data['decisions'][:30]
    ready=[x for x in center['cards'] if x['bucket']=='ready'][:30]+data['ready'][:40];incidents=data['incidents'][:30]+live_incidents
    out='<section class="today420-hero"><div><p class="eyebrow">5.6.21 · ASSISTANT VIVANT</p><h1>Ce qui exige réellement votre attention</h1><p>Les succès techniques sont masqués. Chaque carte correspond à une échéance, un livrable utilisable, une décision ou un incident relançable.</p></div>'+form('production_cycle391','Préparer tout le travail interne disponible',{'limit':'30'})+'</section>'
    # Stable vocabulary remains machine-readable for bookmarks, accessibility
    # helpers and installations upgraded from the 3.9 views. It is not a fifth
    # visible zone in the 4.2 interface.
    out+='<span class="ux392-tabs" hidden>À traiter · Prêt à utiliser · Activité récente · Routage hybride · À faire maintenant · Préparé par AxiorHub · Décision nécessaire · Terminé récemment</span>'
    out+='<nav class="today420-summary"><a href="#today420-todo"><strong>'+str(len(todo))+'</strong>À faire aujourd’hui</a><a href="#today420-ready"><strong>'+str(len(ready))+'</strong>Préparé pour vous</a><a href="#today420-decisions"><strong>'+str(len(decisions))+'</strong>Votre décision</a><a href="#today420-incidents"><strong>'+str(len(incidents))+'</strong>Incidents</a></nav>'
    out+=_zone('todo','À faire aujourd’hui','Échéances, urgences et actions dont le cabinet dépend.',todo,lambda x:_action_card(x,form,link))
    out+=_zone('ready','Préparé pour vous','Brouillons et fichiers effectivement relus depuis leur service de destination.',ready,lambda x:_deliverable_card(x,form,link) if 'deliverable_kind' in x else _action_card(x,form,link))
    out+=_zone('decisions','Votre décision est nécessaire','Ambiguïtés, stratégies et projets qui engagent votre appréciation.',decisions,lambda x:_deliverable_card(x,form,link) if 'deliverable_kind' in x else _action_card(x,form,link))
    out+=_zone('incidents','Incidents','La cause réelle est conservée ; les traitements interrompus peuvent être relancés après contrôle.',incidents,lambda x:_deliverable_card(x,form,link,True) if 'deliverable_kind' in x else _live_incident_card(x,form,link))
    out+='<details class="technical"><summary>Journal technique secondaire</summary><p>Les opérations réussies sans livrable avocat restent consultables dans État du système et Administration.</p>'+link('/etat-systeme','Ouvrir l’état du système')+'</details>'
    return out


def _live_incident_card(item,form,link):
    from .web import JOB_LABELS
    message=item['error'] or 'Worker non confirmé ou traitement trop long ; vérifier l’état avant reprise.'
    out='<article class="result420-card result420-error" data-live-card="incident-'+str(item['id'])+'"><small>'+e(item['matter'] or 'Cabinet')+'</small><h3>'+e(JOB_LABELS.get(item['kind'],'Traitement du cabinet'))+'</h3><p>'+e(message)+'</p>'
    out+=link('/etat-systeme','Voir le diagnostic',job=item['id'])
    if item['status']=='error':out+=form('retry_job393','Relancer',{'job':item['id']})
    return out+'</article>'


def _matter_options(desk, selected=''):
    out=['<option value="">Choisir un dossier…</option>']
    for matter in load_matters(desk.c):
        mid=str(matter['id']);out.append('<option value="'+e(mid)+'"'+(' selected' if mid==selected else '')+'>'+e(matter_option(matter))+'</option>')
    return ''.join(out)


def studio_page(desk, args, form, link):
    selected=str(args.get('matter') or '');dtype=str(args.get('type') or 'conclusions')
    estimate=estimate_studio(desk,dtype,'',[],selected) if dtype in STUDIO_TYPES else None
    labels={'email':'Courriel','letter':'Courrier Word','conclusions':'Conclusions','assignation':'Assignation','contract':'Contrat ou CGV','consultation':'Consultation','hearing_note':'Note de plaidoirie','exhibit_list':'Bordereau de pièces','report':'Compte rendu','internal_note':'Note interne'}
    options=''.join('<option value="'+e(k)+'"'+(' selected' if k==dtype else '')+'>'+e(labels[k])+'</option>' for k in STUDIO_TYPES)
    out='<section class="studio420-hero"><div><p class="eyebrow">STUDIO DE PRODUCTION</p><h1>Du dossier au livrable, dans un seul écran</h1><p>Le dossier ouvert est présélectionné. AxiorHub rassemble les sources, prépare, contrôle puis dépose seulement un travail interne et réversible.</p></div>'+link('/production','Voir les résultats')+'</section>'
    indexed=[]
    if selected:
        from .index import DocumentIndex
        index=DocumentIndex(desk.c['state_dir'])
        indexed=[dict(path=x[0],modified=x[1]) for x in index.db.execute('SELECT path,modified FROM docs WHERE matter=? AND error="" ORDER BY modified DESC,path LIMIT 40',(selected,))]
        index.db.close()
    source_picker='<fieldset class="studio420-wide studio420-sources"><legend>Documents sources du dossier à utiliser</legend>'
    source_picker+=(''.join('<label><input type="checkbox" name="source_paths" value="'+e(x['path'])+'"><span>'+e(x['path'].rsplit('/',1)[-1])+'</span><small>'+e(x['modified'])+'</small></label>' for x in indexed) if indexed else '<p>Aucun document indexé pour le dossier présélectionné.</p>')+'</fieldset>'
    extra='<div class="studio420-grid"><label>Dossier<select required name="matter" data-studio-matter>'+_matter_options(desk,selected)+'</select></label><label>Type de livrable<select required name="deliverable_kind" data-studio-type>'+options+'</select></label>'+source_picker+'<label class="studio420-wide">Autres documents sources — un chemin Nextcloud par ligne<textarea name="source_paths" rows="4" maxlength="50000" placeholder="/CABINET/01 - Dossiers/…/Conclusions.pdf"></textarea></label><label>Modèle Word facultatif<input name="template_id" maxlength="120" placeholder="Sélection automatique"></label><label>Modèle IA<select name="requested_model"><option value="auto">Choisi par le banc du cabinet</option><option value="local">Forcer le local</option><option value="best_authorized">Meilleur modèle autorisé</option></select></label><label class="studio420-wide">Instruction libre ou dictée<textarea required name="instruction" rows="7" maxlength="20000" placeholder="Décrivez le résultat attendu, les contraintes et l’échéance…"></textarea></label><label>Courriel source si réponse<input name="mail_key" maxlength="64" placeholder="Clé du courriel"></label></div><p class="studio420-drop">Sélectionnez les pièces déjà indexées. Pour un nouveau fichier, utilisez le bouton robot et son dépôt sécurisé, puis rattachez-le au dossier. Les sources ne sont jamais remplacées.</p>'
    out+=form('studio_prepare420','Préparer et contrôler',{},extra)
    if estimate:
        out+='<aside class="studio420-estimate"><strong>Estimation initiale</strong><span>Temps : '+str(estimate['estimated_minutes'])+' min</span><span>Modèle : '+e(estimate['provider']+' · '+estimate['model'])+'</span><span>Confidentialité : '+e(estimate['confidentiality'])+'</span><span>Coût estimé : '+format(estimate['estimated_cost_usd'],'.4f')+' $</span></aside>'
    out+='<section class="studio420-catalog"><h2>Livrables disponibles</h2><div>'+''.join('<button type="button" data-studio-choice="'+e(k)+'">'+e(v)+'</button>' for k,v in labels.items())+'</div></section>'
    return out


def metrics_page(desk):
    data=dashboard(desk);m=data['metrics'];cards=(
      (str(m['actionable_mail_coverage_percent'])+' %','Courriels actionnables couverts'),
      (m['verified_imap_drafts'],'Brouillons retrouvés dans IMAP'),
      (m['deliverables'].get('verified',0),'Livrables vérifiés'),
      (str(m['matter_link_rate_percent'])+' %','Rattachement dossier'),
      (str(m['accepted_without_major_change_percent'])+' %','Acceptés sans modification'),
      (str(m['official_citation_verification_percent'])+' %','Citations officielles vérifiées'),
      (str(m['estimated_minutes_saved'])+' min','Temps économisé estimé'),
      (format(m['openrouter_cost_usd'],'.4f')+' $','Coût OpenRouter'))
    breakdown=''
    if m.get('openrouter_cost_by_purpose') or m.get('openrouter_cost_by_matter'):
        breakdown='<details><summary>Détail du coût OpenRouter</summary>'
        if m.get('openrouter_cost_by_purpose'):
            breakdown+='<h3>Par fonction</h3><ul>'+''.join('<li>'+e(x['purpose'])+' : '+format(float(x['cost_usd']),'.4f')+' $ · '+str(x['requests'])+' requête(s)</li>' for x in m['openrouter_cost_by_purpose'])+'</ul>'
        if m.get('openrouter_cost_by_matter'):
            breakdown+='<h3>Par dossier</h3><ul>'+''.join('<li>'+e(x['matter'])+' : '+format(float(x['cost_usd']),'.4f')+' $</li>' for x in m['openrouter_cost_by_matter'])+'</ul>'
        breakdown+='</details>'
    return '<section class="metrics420"><p class="eyebrow">INDICATEURS MÉTIER · '+str(m['period_days'])+' JOURS</p><h2>Ce qu’AxiorHub a réellement produit</h2><div>'+''.join('<article><strong>'+e(value)+'</strong><span>'+e(label)+'</span></article>' for value,label in cards)+'</div><p>Les actions techniques ne sont pas comptées comme des livrables. Un dépôt est compté seulement après relecture depuis IMAP ou Nextcloud.</p>'+breakdown+'</section>'


def production_page(desk,args,form,link):
    data=dashboard(desk)
    out='<section class="studio420-hero compact"><div><p class="eyebrow">4.2 · REGISTRE DES LIVRABLES</p><h1>Productions vérifiées</h1><p>Un résultat métier, son déclencheur, ses sources, sa destination et son contrôle.</p></div>'+link('/studio','Créer un livrable')+'</section>'+metrics_page(desk)
    from .interface410_ui import produce_hub
    out+=produce_hub(link,str(desk.c.get('workspace',{}).get('pdf_url') or ''))
    out+='<details class="technical"><summary>Automatisations de production</summary><h2>Chaîne de production · Production réelle</h2><p>Brouillons IMAP vérifiés et documents Nextcloud relus : l’avocat décide uniquement avant l’engagement externe.</p><div class="actions">'+form('production_cycle390','Lancer la production',{'limit':'30'})+form('advance_playbooks390','Continuer les playbooks',{'limit':'30'})+'</div><p>L’avocat décide avant envoi, dépôt, signature ou facturation définitive.</p></details>'
    out+='<section class="today420-zone"><header><div><h2>Livrables récents</h2><p>Les dépôts en cours de vérification ne sont jamais présentés comme disponibles.</p></div><strong>'+str(len(data['deliverables']))+'</strong></header><div class="today420-grid">'
    out+=''.join(_deliverable_card(x,form,link,x['status']=='error') for x in data['deliverables'][:80]) or '<p class="empty">Aucun livrable 4.2 enregistré.</p>'
    return out+'</div></section>'


def smart_matter_section(desk,matter,form,link):
    mid=matter['id'];data=dashboard(desk);rows=[x for x in data['deliverables'] if x['matter']==mid]
    next_ready=next((x for x in rows if x['status']=='verified'),None)
    incidents=sum(x['status']=='error' for x in rows);decisions=sum(x['status'] in ('prepared','abstained') for x in rows)
    signals=list(desk.db.execute("SELECT title,due,severity FROM proactive_signals WHERE matter=? AND state='open' ORDER BY due,last_seen DESC LIMIT 8",(mid,)))
    due=next((x['due'] for x in signals if x['due']), '')
    out='<section class="matter420"><header><div><p class="eyebrow">DOSSIER INTELLIGENT</p><h2>Prochaine action recommandée</h2><p>'+e(('Échéance repérée : '+due+'. ') if due else '')+'AxiorHub peut actualiser le dossier, reconstruire le graphe arguments–preuves et préparer les travaux internes possibles.</p></div>'+form('advance_matter420','Faire avancer le dossier',{'matter':mid})+'</header><div class="matter420-kpis"><article><strong>'+str(len(rows))+'</strong><span>Productions</span></article><article><strong>'+str(decisions)+'</strong><span>Décisions</span></article><article><strong>'+str(incidents)+'</strong><span>Incidents</span></article><article><strong>'+('Oui' if next_ready else 'Non')+'</strong><span>Livrable prêt</span></article></div>'
    if signals:out+='<details><summary>Événements et échéances détectés</summary><ul>'+''.join('<li>'+e(x['title'])+((' — '+e(x['due'])) if x['due'] else '')+'</li>' for x in signals)+'</ul></details>'
    return out+'<p>'+link('/studio','Ouvrir le Studio avec ce dossier',matter=mid)+'</p></section>'
