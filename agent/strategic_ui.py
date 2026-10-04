"""HTML fragment for the lawyer-facing strategic workspace."""
from .common import matter_display
import json
from html import escape

from .strategic import ACT_TYPES,act_projects,latest_matrix,latest_strategy,summary


def e(value):return escape(str(value if value is not None else ''),quote=True)


def block(value):return '<pre>'+e(value)+'</pre>'


def panel(c,desk,matters,args,form,link):
    mid=args.get('matter','');by_id={m['id']:m for m in matters}
    options='<option value="">Choisir un dossier…</option>'+''.join(
      '<option value="'+e(m['id'])+'"'+(' selected' if m['id']==mid else '')+'>'+e(matter_display(m))+'</option>'
      for m in sorted(matters,key=lambda x:matter_display(x).casefold()))
    out='<section class="strategy-intro"><h2>⚖️ Espace stratégique</h2><p>Analyse contradictoire, matrice de preuve et projets de travail. Tout résultat reste interne, sourcé et soumis à votre validation.</p>'
    out+='<form method="get" enctype="application/x-www-form-urlencoded"><label>Dossier<select name="matter" required>'+options+'</select></label><button>Ouvrir</button></form></section>'
    if not mid:return out+'<p class="empty">Choisissez un dossier. Aucune analyse transversale ne fusionne les faits de plusieurs affaires.</p>'
    m=by_id.get(mid)
    if not m:return out+'<p class="notice">Dossier introuvable.</p>'
    info=summary(desk,mid)
    out+='<section class="matter-hero"><div><h2>'+e(matter_display(m))+'</h2><p>'+e(mid+' · '+m['path'])+'</p></div>'+link('/matter','Retour à la fiche',id=mid)+'</section>'
    out+='<div class="stats"><article><strong>'+str(info['strategies'])+'</strong><span>Analyses</span></article><article><strong>'+str(info['matrix_rows_to_validate'])+'</strong><span>Lignes à vérifier</span></article><article><strong>'+str(info['act_projects'])+'</strong><span>Projets d’actes</span></article><article><strong>'+str(info['validated_acts'])+'</strong><span>Projets validés</span></article></div>'
    out+='<section><h2>1. Analyse stratégique</h2><p>Indiquez le but recherché. L’agent comparera les options, risques, preuves manquantes et recherches juridiques nécessaires.</p>'
    out+=form('analyze_strategy','Analyser les options',{'matter':mid},'<label>Objectif ou question stratégique<textarea name="objective" required maxlength="3000" rows="4" placeholder="Ex. Évaluer l’opportunité d’une transaction avant l’audience et les pièces encore nécessaires."></textarea></label>')+'</section>'
    analysis=latest_strategy(desk,mid)
    if analysis:
        data=analysis['data'];out+='<section class="strategy-result"><div class="section-heading"><div><h2>Analyse n°'+str(analysis['version'])+'</h2><p>'+e(analysis['objective'])+'</p></div><span class="status '+e(analysis['status'])+'">'+e(analysis['status'])+'</span></div>'+block(data['executive_summary'])
        if data['recommended_approach']:
            rec=data['recommended_approach'];out+='<article class="recommendation"><h3>Orientation proposée</h3>'+block(rec['summary'])
            if rec['conditions']:out+='<ul>'+''.join('<li>'+e(x)+'</li>' for x in rec['conditions'])+'</ul>'
            out+='<small>Sources : '+e(', '.join(rec['source_ids']))+'</small></article>'
        out+='<div class="strategy-grid">'
        for option in data['options']:
            out+='<article class="strategy-option '+e(option['orientation'])+'"><small>'+e(option['orientation'])+'</small><h3>'+e(option['title'])+'</h3><p>'+e(option['description'])+'</p>'
            for field,label in [('strengths','Forces'),('weaknesses','Faiblesses'),('risks','Risques'),('missing_evidence','Preuves manquantes'),('next_steps','Étapes possibles')]:
                if option[field]:out+='<h4>'+label+'</h4><ul>'+''.join('<li>'+e(x)+'</li>' for x in option[field])+'</ul>'
            out+='<small>Sources : '+e(', '.join(option['source_ids']))+'</small></article>'
        out+='</div>'
        for field,label in [('procedural_risks','Risques procéduraux à vérifier'),('contradictions','Contradictions'),('questions_for_lawyer','Décisions attendues'),('limits','Limites de l’analyse')]:
            if data[field]:out+='<h3>'+label+'</h3><ul>'+''.join('<li>'+e(x)+'</li>' for x in data[field])+'</ul>'
        fields={'matter':mid,'analysis':analysis['id']}
        out+='<div class="actions">'+form('validate_strategy','Valider comme note de travail',fields)+form('archive_strategy','Archiver',fields)+'</div><details><summary>Sources fournies</summary>'
        for source in analysis['sources']:out+='<p><strong>'+e(source.get('path') or source['id'])+'</strong><br><small>'+e(source['id'])+'</small></p>'+block(source.get('excerpt','')[:1500])
        out+='</details></section>'
    out+='<section><h2>2. Matrice faits / pièces / prétentions</h2><p>Chaque ligne conserve séparément les éléments favorables, contraires, neutres et manquants.</p>'+form('build_matrix','Construire ou actualiser la matrice',{'matter':mid})+'</section>'
    matrix=latest_matrix(desk,mid)
    if matrix:
        source_labels={x['id']:(x.get('path') or x['id'])+' · '+x['id'] for x in matrix['sources']}
        shown=lambda ids:'\n'.join(source_labels.get(x,x) for x in ids) or '—'
        out+='<section><h2>Matrice n°'+str(matrix['version'])+'</h2><p>'+e(matrix['objective'])+'</p><div class="matrix-table"><table><thead><tr><th>Élément</th><th>État de preuve</th><th>Pièces favorables</th><th>Pièces contraires</th><th>Manques</th><th>Contrôle</th></tr></thead><tbody>'
        labels={'documented':'Documenté','partially_documented':'Partiellement documenté','alleged':'Allégué','contradicted':'Contredit','unknown':'Inconnu'}
        for row in matrix['rows']:
            fields={'matter':mid,'row':row['id']}
            controls=form('validate_matrix_row','Valider',fields)+form('dispute_matrix_row','Contester',fields)+form('archive_matrix_row','Archiver',fields)
            out+='<tr><td><small>'+e(row['row_type']+' · '+row['asserted_by'])+'</small><strong>'+e(row['proposition'])+'</strong><p>'+e(row['strategic_use'])+'</p></td><td><span class="proof '+e(row['proof_status'])+'">'+e(labels.get(row['proof_status'],row['proof_status']))+'</span><small>'+e(row['status'])+'</small></td><td>'+e(shown(row['supporting_sources']))+'</td><td>'+e(shown(row['contradicting_sources']))+'</td><td>'+e('\n'.join(row['missing_evidence']) or '—')+'</td><td><div class="compact-actions">'+controls+'</div></td></tr>'
        out+='</tbody></table></div>'
        if matrix['data']['global_gaps']:out+='<h3>Lacunes globales</h3><ul>'+''.join('<li>'+e(x)+'</li>' for x in matrix['data']['global_gaps'])+'</ul>'
        out+='</section>'
    type_options=''.join('<option value="'+e(key)+'">'+e(label)+'</option>' for key,label in ACT_TYPES.items())
    out+='<section><h2>3. Atelier de projets d’actes</h2><p>Le résultat est une trame interne : aucun fichier n’est déposé dans Nextcloud et aucun acte n’est envoyé ou déposé.</p>'+form('draft_act','Préparer le projet',{'matter':mid},'<label>Type de projet<select name="act_type" required>'+type_options+'</select></label><label>Instructions de rédaction<textarea name="instruction" required maxlength="3000" rows="5" placeholder="Objet, position à soutenir, destinataire et contraintes particulières."></textarea></label>')+'</section>'
    projects=act_projects(desk,mid)
    if projects:
        out+='<section><h2>Projets disponibles</h2>'
        for project in projects:
            fields={'matter':mid,'project':project['id']}
            out+='<details class="act-project"><summary>'+e('n°'+str(project['version'])+' · '+ACT_TYPES.get(project['act_type'],project['act_type'])+' · '+project['title']+' · '+project['status'])+'</summary>'+block(project['content'])+'<div class="actions">'+form('validate_act','Valider comme projet de travail',fields,('<label><input type="checkbox" name="acknowledge_unverified" value="yes"> J’ai contrôlé les références juridiques signalées « à vérifier »</label>' if ((project['data'].get('_quality') or {}).get('citations') or {}).get('needs_check') else ''))+form('archive_act','Archiver',fields)+'</div><p class="notice">Validation interne seulement : elle ne vaut ni signature, ni dépôt, ni envoi.</p></details>'
        out+='</section>'
    return out
