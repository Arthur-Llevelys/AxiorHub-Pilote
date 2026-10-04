"""Simplified lawyer-facing views for AxiorHub 3.9.2."""
from html import escape
import json

from .common import load_matters
from .improvements36 import matter_label
from .learning392 import PURPOSES, snapshot
from .operating380 import action_center
from .production391 import dashboard


def e(value):return escape(str(value if value is not None else ''),quote=True)


def _matter_names(desk):return {m['id']:matter_label(m) for m in load_matters(desk.c)}


def _action(card, link):
    out='<article class="ux392-item"><div><small>'+e(card.get('matter_name') or 'Cabinet')+' · confiance '+str(card.get('confidence',0))+' %</small><h3>'+e(card.get('title'))+'</h3><p>'+e(card.get('reason'))+'</p></div><div class="ux392-actions">'
    href=str(card.get('href') or '')
    if href.startswith('/'):
        from urllib.parse import parse_qsl,urlsplit
        target=urlsplit(href);out+=link(target.path,card.get('action_label') or 'Ouvrir',**dict(parse_qsl(target.query,keep_blank_values=True)))
    else:out+=link('/production','Examiner')
    return out+'</div></article>'


def _incident(item, names, form, link):
    label=names.get(item['matter'],item['matter'] or 'Cabinet')
    out='<article class="ux392-item ux392-incident"><div><small>'+e(label)+' · étape '+e(item['stage'])+'</small><h3>Production interrompue</h3><p>'+e(item['last_error'] or 'Cause non renseignée')+'</p></div><div class="ux392-actions">'
    if item['matter']:out+=link('/matter','Ouvrir le dossier',id=item['matter'])
    out+=form('retry_production391','Relancer',{'job_id':item['job_id']})
    return out+'</div></article>'


def today_page(desk, form, link):
    center=action_center(desk);prod=dashboard(desk);names=_matter_names(desk)
    urgent=[x for x in center['cards'] if x['bucket']=='urgent']
    decisions=[x for x in center['cards'] if x['bucket']=='decision']
    ready=[x for x in center['cards'] if x['bucket']=='ready']
    incidents=prod['incidents']
    recent=[x for x in prod['flows'] if x['status']=='verified'][:10]
    priority_count=len(urgent)+len(decisions)+len(incidents)
    out='<section class="ux392-hero"><div><p class="eyebrow">VERSION 4.1 · EXPÉRIENCE UTILISATEUR</p><h1>Votre journée, en trois vues</h1><p>Décidez, utilisez ce qui est prêt, puis consultez l’historique seulement si nécessaire.</p></div><div class="ux392-hero-actions">'+form('production_cycle391','Préparer le travail disponible',{'limit':'30'})+link('/apprentissage','Apprentissage métier')+link('/routage-hybride','Routage hybride')+'</div></section>'
    out+='<nav class="ux392-tabs" aria-label="Résumé du jour"><a href="#ux-priority"><strong>'+str(priority_count)+'</strong><span>À traiter</span></a><a href="#ux-ready"><strong>'+str(len(ready))+'</strong><span>Prêt à utiliser</span></a><a href="#ux-recent"><strong>'+str(len(recent))+'</strong><span>Activité récente</span></a></nav>'
    out+='<section id="ux-priority" class="ux392-section"><div class="ux392-section-head"><div><h2>À traiter</h2><p>Une seule liste pour les urgences, décisions et incidents.</p></div><span>'+str(priority_count)+'</span></div><div class="ux392-list">'
    for item in urgent[:8]+decisions[:8]:out+=_action(item,link)
    for item in incidents[:8]:out+=_incident(item,names,form,link)
    if not priority_count:out+='<p class="empty">Aucune décision immédiate.</p>'
    out+='</div></section><section id="ux-ready" class="ux392-section"><div class="ux392-section-head"><div><h2>Prêt à utiliser</h2><p>Brouillons et documents préparés, avec un accès direct à la relecture.</p></div><span>'+str(len(ready))+'</span></div><div class="ux392-list">'
    for item in ready[:16]:out+=_action(item,link)
    if not ready:out+='<p class="empty">Aucun nouveau livrable prêt.</p>'
    out+='</div></section><details id="ux-recent" class="ux392-section ux392-history"><summary><strong>Activité récente</strong><span>'+str(len(recent))+'</span></summary><div class="ux392-list">'
    for item in recent:
        label=names.get(item['matter'],item['matter'] or 'Cabinet')
        out+='<article class="ux392-item"><div><small>'+e(label)+' · '+e(item['updated'])+'</small><h3>'+e(item.get('output_label') or item['job_kind'].replace('_',' '))+'</h3></div><div class="ux392-actions">'+(link('/production','Voir',output=item['output_id']) if item.get('output_id') else '')+'</div></article>'
    if not recent:out+='<p class="empty">Aucune production récente.</p>'
    out+='</div></details><p class="ux392-legacy">Les anciennes rubriques « À faire maintenant », « Décision nécessaire » et « Incidents » sont réunies dans « À traiter ». « Préparé par AxiorHub » devient « Prêt à utiliser » et « Terminé récemment » devient « Activité récente ».</p>'
    return out


def review_panel(desk, output_id, form, link):
    row=desk.db.execute('SELECT * FROM production_outputs_v390 WHERE id=?',(str(output_id or ''),)).fetchone()
    if not row:return '<p class="notice">Le livrable demandé n’existe plus.</p>'
    paths=json.loads(row['paths'] or '[]');detail=json.loads(row['detail'] or '{}')
    from .learning392 import learning_context
    learned=learning_context(desk,row['matter'],'document_drafting' if row['output_kind']!='mail_draft' else 'mail_drafting',record=False)
    out='<section class="review392"><header><div><p class="eyebrow">RELECTURE SIMPLE</p><h2>'+e(row['label'])+'</h2><p>'+e(row['matter'] or 'Cabinet')+' · '+e(row['updated'])+'</p></div>'+link('/production','Fermer')+'</header>'
    out+='<div class="review392-status"><span>1. Lire</span><span>2. Comparer les sources</span><strong>3. Décider</strong></div><div class="review392-body"><article><h3>Livrable</h3>'
    if paths:out+='<ul class="review392-paths">'+''.join('<li>'+e(x)+'</li>' for x in paths)+'</ul>'
    else:out+='<p class="empty">Le livrable se trouve dans la messagerie ou le registre interne.</p>'
    out+='<div class="review392-preview">'+e(detail.get('message') or 'Ouvrez le fichier ou le brouillon pour relire son contenu complet.')+'</div>'
    if row['matter']:out+='<p>'+link('/matter','Ouvrir le dossier et les versions',id=row['matter'])+'</p>'
    out+='</article><aside><h3>Contrôle utile</h3><dl><dt>Source</dt><dd>'+e(row['source_id'])+'</dd><dt>Traitement</dt><dd>'+e(row['job_kind'])+'</dd><dt>État</dt><dd>'+e(row['status'])+'</dd></dl><h3>Apprentissage appliqué</h3><p>'+str(len(learned['corrections']))+' préférence(s) active(s) · '+str(len(learned['approved_templates']))+' modèle(s) approuvé(s).</p><p>'+link('/apprentissage','Voir ce qu’AxiorHub retient')+'</p></aside></div>'
    out+='<footer class="review392-decisions">'+form('review_output391','Accepter',{'output_id':row['id'],'decision':'accepted'})
    learning_fields='<div class="review392-fields"><label>Portée de la règle<select name="rule_scope"><option value="matter">Ce dossier</option><option value="client">Ce client</option><option value="matter_type">Ce type de dossier</option><option value="cabinet">Tout le cabinet</option></select></label><label>Valeur si client ou type<input name="scope_value" maxlength="240"></label><label>Fonction<select name="rule_purpose"><option value="'+('mail_drafting' if row['output_kind']=='mail_draft' else 'document_drafting')+'">Ce type de production</option><option value="all">Toutes les fonctions</option></select></label><label>Nature<select name="rule_type"><option value="style">Style</option><option value="structure">Structure</option><option value="recipient">Destinataires</option><option value="subject">Objet</option><option value="legal_position">Position juridique</option><option value="prohibited_claim">Interdiction</option></select></label><input type="hidden" name="source_kind" value="structure"></div><label>Passage proposé avant correction<textarea required name="original" rows="7" maxlength="12000"></textarea></label><label>Passage corrigé<textarea required name="corrected" rows="7" maxlength="12000"></textarea></label><label>Consigne réutilisable — règle sans fait propre au dossier<textarea required name="guidance" rows="3" maxlength="1500" placeholder="Toujours… / Ne jamais…"></textarea></label>'
    out+='<details><summary>Modifier et apprendre — transformer en règle</summary>'+form('review_output391','Enregistrer la correction et la règle',{'output_id':row['id'],'decision':'modified'},learning_fields)+'</details>'
    out+='<details><summary>Rejeter</summary>'+form('review_output391','Rejeter le livrable',{'output_id':row['id'],'decision':'rejected'},'<label>Motif<textarea required name="note" rows="3" maxlength="3000"></textarea></label>')+'</details></footer></section>'
    return out


def learning_page(desk, form):
    data=snapshot(desk);out='<section class="ux392-hero"><div><p class="eyebrow">APPRENTISSAGE MÉTIER</p><h1>Ce qu’AxiorHub retient</h1><p>Vos corrections validées orientent les prochains brouillons. Vos modèles Word approuvés servent de références de forme. Tout reste visible et réversible.</p></div></section>'
    out+='<section class="learning392-kpis"><article><strong>'+str(data['active_corrections'])+'</strong><span>Préférences actives</span></article><article><strong>'+str(data['approved_templates'])+'</strong><span>Modèles approuvés</span></article><article><strong>'+str(data['reviewed_outputs'])+'</strong><span>Livrables évalués</span></article><article><strong>'+str(data['useful_rate_percent'])+' %</strong><span>Acceptés ou corrigés</span></article></section>'
    out+='<section class="card"><div class="ux392-section-head"><div><h2>Préférences apprises</h2><p>Seule la consigne réutilisable est transmise au modèle, jamais les faits du dossier ayant conduit à la correction.</p></div></div><div class="learning392-rules">'
    for item in data['corrections']:
        scope='Cabinet' if item['scope']=='general' else ('Dossier '+item['matter'] if item['matter'] else item['scope'])
        out+='<article class="learning392-rule '+('is-paused' if not item['enabled'] else '')+'"><div><small>'+e(scope)+' · '+e(item['source_kind'])+'</small><h3>'+e(item['guidance'])+'</h3><p>'+('Active' if item['enabled'] else 'En pause')+'</p></div>'+form('set_learning_rule392','Mettre en pause' if item['enabled'] else 'Réactiver',{'correction_id':item['id'],'enabled':'no' if item['enabled'] else 'yes'})+'</article>'
    if not data['corrections']:out+='<p class="empty">Aucune correction métier validée. Corrigez un livrable depuis la relecture pour créer votre première préférence.</p>'
    out+='</div></section><section class="card"><h2>Modèles Word approuvés</h2><div class="learning392-templates">'
    for item in data['templates']:
        out+='<article><h3>'+e(item['label'])+'</h3><p>Version '+str(item['version'])+' · '+str(len(item['placeholders']))+' champ(s) contrôlé(s)</p><small>Empreinte '+e(item['sha256'][:16])+'…</small></article>'
    if not data['templates']:out+='<p class="empty">Aucun modèle approuvé. Importez et contrôlez vos modèles dans Documents et Word.</p>'
    out+='</div></section><section class="card"><h2>Utilisation réelle sur '+str(data['period_days'])+' jours</h2><div class="learning392-uses">'
    for item in data['uses']:
        out+='<article><strong>'+str(item['uses'])+'</strong><span>'+e(PURPOSES.get(item['purpose'],item['purpose']))+'</span><small>'+str(item['matters'])+' dossier(s)</small></article>'
    if not data['uses']:out+='<p class="empty">Aucune préférence n’a encore été injectée dans un traitement 3.9.2.</p>'
    out+='</div><p class="notice">AxiorHub n’apprend jamais automatiquement un fait, un montant, une stratégie, une identité de partie ou une règle de droit. Une correction ne devient active qu’après votre action « Modifier et apprendre ».</p></section>'
    return out
