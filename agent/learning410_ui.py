"""Interface d'apprentissage métier explicable AxiorHub 4.1."""
from html import escape

from .learning392 import snapshot as legacy_snapshot
from .learning410 import snapshot
from .common import load_matters


def e(value): return escape(str(value if value is not None else ''), quote=True)


def page(desk, form):
    data = snapshot(desk, 30); legacy = legacy_snapshot(desk, 30)
    matters=load_matters(desk.c)
    suggested=sorted({str(value).strip() for matter in matters for value in
      (matter.get('id',''),matter.get('client_name',''),matter.get('matter_type',''),matter.get('type','')) if str(value).strip()})
    scope_values='<datalist id="learn410-scope-values">'+''.join('<option value="'+e(x)+'">' for x in suggested)+'</datalist>'
    out = '''<section class="learn410-hero"><div><p class="eyebrow">4.1 · APPRENTISSAGE MÉTIER</p>
      <h1>Ce qu’AxiorHub retient</h1>
      <p>AxiorHub ne réentraîne aucun modèle. Il applique uniquement les règles que vous avez approuvées, selon leur portée, puis journalise leur utilisation.</p></div></section>'''
    out += '<section class="learn410-kpis">'
    for value, label in ((data['active_rules'],'Règles 4.1 actives'),
      (data['correction_volume'],'Corrections sur 30 jours'),
      (data['changed_characters'],'Caractères corrigés'),
      (legacy['approved_templates'],'Modèles Word approuvés'),
      (len(data['trusted_documents']),'Documents fiables'),
      (data['rule_uses'],'Règles réellement utilisées')):
        out += '<article><strong>'+str(value)+'</strong><span>'+e(label)+'</span></article>'
    out += '</section><section class="card learn410-create"><h2>Créer une règle métier</h2><p>Exemples : rappeler la référence dans l’objet ; ne jamais écrire au client adverse ; employer le vouvoiement ; ne pas annoncer de chances de succès numériques.</p>'
    fields = '''<div class="learn410-grid"><label>Portée<select name="scope" required>
      <option value="cabinet">Tout le cabinet</option><option value="matter_type">Type de dossier</option>
      <option value="client">Client</option><option value="matter">Dossier précis</option></select></label>
      <label>Valeur de portée<input name="scope_value" list="learn410-scope-values" maxlength="240" placeholder="Choisir le client, type ou dossier"></label>
      <label>Fonction<select name="purpose"><option value="all">Toutes</option><option value="mail_drafting">Courriels</option><option value="mail_triage">Tri des courriels</option><option value="document_drafting">Conclusions et contrats</option><option value="hearing">Audiences</option><option value="assistant">Assistant</option><option value="word_revision">Révision Word</option><option value="control">Contrôle</option></select></label>
      <label>Nature<select name="rule_type"><option value="style">Style et formulation</option><option value="structure">Structure</option><option value="recipient">Destinataires</option><option value="subject">Objet du courriel</option><option value="legal_position">Position juridique récurrente</option><option value="prohibited_claim">Affirmation interdite</option><option value="word_template">Modèle Word</option><option value="other">Autre</option></select></label></div>
      <label>Instruction réutilisable<textarea name="instruction" required rows="4" maxlength="2000" placeholder="Toujours… / Ne jamais…"></textarea></label>'''
    out += scope_values+form('save_business_rule410','Enregistrer la règle',extra=fields)+'</section>'
    out += '<section class="card"><h2>Préférences apprises — règles actives et réversibles</h2><div class="learn410-rules">'
    for item in data['rules']:
        measured='Utilisée '+str(item.get('uses',0))+' fois · '+str(item.get('influenced_productions',0))+' production(s) influencée(s)'
        if item.get('acceptance_percent') is not None:measured+=' · '+str(item['acceptance_percent'])+' % acceptées'
        out += '<article class="'+('is-paused' if item['status']!='active' else '')+'"><div><small>'+e(item['scope']+(' · '+item['scope_value'] if item['scope_value'] else '')+' · '+item['purpose']+' · v'+str(item['version']))+'</small><h3>'+e(item['instruction'])+'</h3><p>'+e(item['rule_type']+' · '+item['status']+' · '+item['origin'])+'</p><p><strong>Mesure :</strong> '+e(measured)+'</p><p>'+e(item.get('recommendation',''))+'</p>'
        if item.get('possible_conflicts'):out+='<p class="warning">Contradiction potentielle avec les règles '+e(', '.join(map(str,item['possible_conflicts'])))+'.</p>'
        if item.get('simulation'):out+='<details><summary>Simuler sur '+str(len(item['simulation']))+' ancien(s) exemple(s)</summary><ul>'+''.join('<li>Dossier '+e(x['matter'] or 'Cabinet')+' · décision '+e(x['decision'])+'<br><small>Avant : '+e(x['original'])+'<br>Après : '+e(x['corrected'])+'</small></li>' for x in item['simulation'])+'</ul></details>'
        out += '</div><div class="actions">'
        if item['status']=='active': out += form('set_business_rule410','Mettre en pause',{'rule_id':item['id'],'status':'paused'})
        else: out += form('set_business_rule410','Réactiver',{'rule_id':item['id'],'status':'active'})
        out += form('set_business_rule410','Archiver',{'rule_id':item['id'],'status':'archived'})+'</div></article>'
    if not data['rules']: out += '<p class="empty">Aucune règle 4.1. Corrigez un livrable ou créez la première règle ci-dessus.</p>'
    out += '</div></section>'
    if data.get('rule_suggestions'):
        out+='<section class="card"><h2>Corrections pouvant devenir une règle</h2><p>Ces corrections restent des suggestions. Une correction isolée n’est jamais généralisée sans votre accord.</p><ul>'+''.join('<li><strong>'+e(x['purpose'])+'</strong> · dossier '+e(x['matter'] or 'Cabinet')+' · '+str(x['added_paragraphs'])+' ajout(s), '+str(x['deleted_paragraphs'])+' suppression(s)</li>' for x in data['rule_suggestions'][:10])+'</ul></section>'
    out += '<section class="card"><h2>Déclarer un document fiable</h2><p>La fiabilité est liée au chemin et à l’empreinte SHA-256 exacts. Une modification du fichier invalide cette référence.</p>'
    trust = '''<div class="learn410-grid"><label>Portée<select name="scope"><option value="cabinet">Cabinet</option><option value="client">Client</option><option value="matter">Dossier</option></select></label><label>Valeur<input name="scope_value" maxlength="240"></label><label>Niveau<select name="trust_level"><option value="reference">Référence de travail</option><option value="approved">Document approuvé</option><option value="authoritative">Source faisant autorité</option></select></label></div><label>Chemin exact<input required name="path" maxlength="2000"></label><label>SHA-256<input required name="sha256" pattern="[a-fA-F0-9]{64}" maxlength="64"></label><label>Motif de confiance<textarea required name="reason" rows="2" maxlength="1000"></textarea></label>'''
    out += form('trust_document410','Enregistrer la référence fiable',extra=trust)+'</section>'
    out += '<section class="card"><h2>Corrections et rejets récents</h2><div class="table"><table><thead><tr><th>Date</th><th>Décision</th><th>Fonction</th><th>Volume</th><th>Règle</th><th>Motif</th></tr></thead><tbody>'
    for item in data['corrections']:
        out += '<tr><td>'+e(item['created'][:16])+'</td><td>'+e(item['decision'])+'</td><td>'+e(item['purpose'])+'</td><td>+'+str(item['added_paragraphs'])+' / −'+str(item['deleted_paragraphs'])+' § · '+str(item['changed_characters'])+' car.</td><td>'+e(item['rule_id'] or '—')+'</td><td>'+e(item['rejection_reason'] or '—')+'</td></tr>'
    out += '</tbody></table></div><p class="notice">Une correction brute reste dans le coffre local. Seule l’instruction approuvée et applicable à la portée choisie est présentée au modèle.</p></section>'
    return out
