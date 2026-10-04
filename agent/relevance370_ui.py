"""Friendly, server-rendered controls for relevance and MCP extensions."""
from .relevance370 import AUTOMATION_LEVELS, list_rules, quality_snapshot
from .extensions364 import list_items
from .improvements36 import matter_option


def rules_page(desk, matters, form):
    from .web import e
    current=desk.settings('automation:level','assisted')
    out='<section class="card"><h2>Niveau d’autonomie</h2><p>Le niveau règle la préparation interne. Aucun niveau n’autorise l’envoi, la signature, le dépôt, le paiement ou une suppression externe.</p><div class="ws-control-grid">'
    for key,item in AUTOMATION_LEVELS.items():
        out+='<article class="ws-control-card"><h3>'+e(item['label'])+(' · actif' if key==current else '')+'</h3><p>'+e(item['description'])+'</p>'+form('set_automation_level370','Activer ce niveau',{'level':key})+'</article>'
    out+='</div></section><section class="card"><h2>Règles de traitement des courriels</h2><p>Les règles sont appliquées dans l’ordre de priorité et restent explicables dans le journal. Une règle « Ignorer » classe le message ; « À vérifier » et « Prioritaire » guident la revue sans envoyer de réponse.</p>'
    out+=form('save_mail_rule370','Ajouter la règle',{},
      '<div class="ws-control-grid"><label>Nom<input required name="name" maxlength="120" placeholder="Notifications de la plateforme"></label>'
      '<label>Priorité<input required type="number" name="priority" min="1" max="999" value="100"></label>'
      '<label>Champ<select name="field"><option value="sender">Expéditeur</option><option value="domain">Domaine</option><option value="subject">Objet</option><option value="recipient">Destinataire</option></select></label>'
      '<label>Condition<select name="operator"><option value="equals">est exactement</option><option value="contains">contient</option><option value="ends_with">se termine par</option></select></label>'
      '<label>Valeur<input required name="value" maxlength="300"></label>'
      '<label>Décision<select name="rule_action"><option value="review">À vérifier</option><option value="priority">Prioritaire</option><option value="ignore">Ignorer</option></select></label></div><input type="hidden" name="enabled" value="yes">')
    rows=list_rules(desk)
    if rows:
        out+='<div class="table"><table><thead><tr><th>Priorité</th><th>Règle</th><th>Condition</th><th>Effet</th><th></th></tr></thead><tbody>'
        for row in rows:
            out+='<tr><td>'+str(row['priority'])+'</td><td>'+e(row['name'])+'</td><td>'+e(row['field']+' '+row['operator']+' '+row['value'])+'</td><td>'+e(row['action'])+'</td><td>'+form('delete_mail_rule370','Supprimer',{'rule_id':row['id'],'confirm':'yes'})+'</td></tr>'
        out+='</tbody></table></div>'
    else:out+='<p class="empty">Aucune règle graphique. Les contrôles déterministes et anti-spam existants restent actifs.</p>'
    out+='</section><section class="card"><h2>Apprendre d’une correction validée</h2><p>Enregistrez seulement une préférence de style, de classement, d’association ou de structure. Les faits, montants, stratégies et règles de droit ne sont jamais généralisés.</p>'
    options='<option value="">Aucun dossier</option>'+''.join('<option value="'+e(m['id'])+'">'+e(matter_option(m))+'</option>' for m in matters)
    out+=form('record_correction370','Enregistrer la correction',{},
      '<div class="ws-control-grid"><label>Portée<select name="scope"><option value="general">Cabinet</option><option value="matter">Dossier choisi</option><option value="mail">Courriel uniquement</option><option value="document">Document uniquement</option></select></label>'
      '<label>Dossier si nécessaire<select name="matter">'+options+'</select></label><label>Type<select name="source_kind"><option value="style">Style</option><option value="classification">Classement</option><option value="association">Association au dossier</option><option value="structure">Structure du document</option></select></label></div>'
      '<label>Proposition initiale<textarea required name="original" rows="4" maxlength="12000"></textarea></label><label>Version corrigée<textarea required name="corrected" rows="4" maxlength="12000"></textarea></label><label>Règle à retenir, sans fait propre au dossier<textarea required name="guidance" rows="3" maxlength="1500"></textarea></label>')
    return out+'</section>'


def mcp_page(desk, form):
    from .web import e
    from .ai_gateway import PURPOSES
    items=list_items(desk)
    purposes=''.join('<label class="ws-check"><input type="checkbox" name="purposes" value="'+e(key)+'"> '+e(label)+'</label>' for key,(label,_) in PURPOSES.items())
    out='<section class="card"><h2>Connecteurs MCP, skills et plugins</h2><p>Ajoutez une fiche Lawve.ai, contrôlez son endpoint et ses permissions, puis activez-la après revue. Les secrets vont dans le coffre local et ne sont jamais réaffichés.</p>'
    out+=form('register_lawve_extension','Enregistrer sans activer',{},
      '<label>URL de la fiche Lawve.ai<input type="url" required name="source_url" placeholder="https://lawve.ai/fr/connectors/auteur/nom"></label><div class="ws-control-grid">'
      '<label>Nom affiché<input name="name" maxlength="160"></label><label>Endpoint HTTPS MCP (connecteur)<input type="url" name="endpoint" maxlength="1000"></label><label>Authentification<select name="auth_type"><option value="none">Aucune</option><option value="bearer">Jeton Bearer</option></select></label><label>Jeton — jamais réaffiché<input type="password" name="token" autocomplete="new-password" maxlength="4096"></label><label>Licence annoncée<input name="license" maxlength="80"></label></div><fieldset><legend>Fonctions autorisées</legend>'+purposes+'</fieldset><label class="ws-check"><input type="checkbox" name="allow_external" value="yes"> J’autorise l’envoi des seules données nécessaires aux fonctions cochées</label>')
    out+='<div class="ws-control-grid">'
    for item in items:
        test=item.get('last_test') or {};status=test.get('message') or test.get('error') or item.get('status','non testé')
        out+='<article class="ws-control-card"><h3>'+e(item.get('name') or item.get('id'))+'</h3><p>'+e(item.get('kind',''))+' · '+('activé' if item.get('enabled') else 'désactivé')+' · '+e(status)+'</p><p>'+e(', '.join(item.get('purposes',[])) or 'Aucune fonction autorisée')+'</p><a target="_blank" rel="noopener noreferrer" href="'+e(item.get('source_url',''))+'">Voir la fiche source</a><div class="actions">'+form('test_lawve_extension','Tester',{'extension_id':item['id']})
        out+=form('set_lawve_extension','Désactiver' if item.get('enabled') else 'Activer après revue',{'extension_id':item['id'],'enabled':'no' if item.get('enabled') else 'yes'},'' if item.get('enabled') else '<label class="ws-check"><input type="checkbox" name="reviewed" value="yes" required> Licence, permissions et rapport vérifiés</label>')+'</div></article>'
    out+='</div>'
    if not items:out+='<p class="empty">Aucun connecteur, skill ou plugin enregistré.</p>'
    return out+'<p class="notice">Les scripts, binaires et commandes stdio restent bloqués. L’activation seule ne transmet aucun dossier.</p></section>'


def quality_summary(desk):
    from .web import e
    data=quality_snapshot(desk);reviews=data['control_reviews']
    return ('<section class="card"><h2>Qualité détaillée</h2><div class="stats">'
      '<article><strong>'+str(data['active_mail_rules'])+'</strong><span>Règles actives</span></article>'
      '<article><strong>'+str(data['active_corrections'])+'</strong><span>Corrections approuvées</span></article>'
      '<article><strong>'+str(sum(reviews.values()))+'</strong><span>Contrôles second modèle</span></article>'
      '<article><strong>'+e(data['automation_level'])+'</strong><span>Niveau d’autonomie</span></article></div>'
      '<p>Contrôles : '+e(', '.join(k+' '+str(v) for k,v in reviews.items()) or 'aucun')+'. Le contrôle est indépendant uniquement lorsque le modèle de contrôle diffère du modèle principal.</p></section>')
