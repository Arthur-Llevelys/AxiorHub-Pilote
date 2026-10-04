"""Banc d'évaluation juridique comparatif AxiorHub 4.1."""
from html import escape
import json

from .evaluation410 import dashboard
from .ai_gateway import public_providers


def e(value): return escape(str(value if value is not None else ''), quote=True)


def page(desk, form):
    data=dashboard(desk);providers=public_providers(desk.c)
    out='<section class="eval410-hero"><p class="eyebrow">4.1 · BANC JURIDIQUE</p><h1>Banc d’évaluation métier et juridique</h1><p>Choisissez un modèle sur les performances du cabinet. Les mêmes cas anonymisés sont soumis aux modèles sélectionnés ; le classement conserve toujours le nombre de cas, les hallucinations, le délai et le coût.</p></section>'
    out+='<section class="eval410-kpis"><article><strong>'+str(len(data['cases']))+'</strong><span>Cas anonymisés</span></article><article><strong>'+str(data['correction_volume_30d'])+'</strong><span>Corrections avocat · 30 j</span></article><article><strong>'+str(data['changed_characters_30d'])+'</strong><span>Caractères corrigés · 30 j</span></article><article><strong>'+str(len(data['leaderboard']))+'</strong><span>Modèles mesurés</span></article></section>'
    out+='<section class="card"><h2>Classement des modèles</h2><div class="table"><table><thead><tr><th>Modèle</th><th>Score métier</th><th>Cas</th><th>Hallucinations</th><th>Délai moyen</th><th>Coût externe</th><th>Corrections avocat</th></tr></thead><tbody>'
    for item in data['leaderboard']:
        out+='<tr><td><strong>'+e(item['provider']+':'+item['model'])+'</strong></td><td>'+str(item['score'])+' %</td><td>'+str(item['samples'])+'</td><td>'+str(item['hallucinations'])+'</td><td>'+str(item['latency_ms'])+' ms</td><td>'+str(item['cost_usd'])+' $</td><td>'+str(item['lawyer_changed_characters'])+' car. · '+str(item['lawyer_reviews'])+' relecture(s)</td></tr>'
    if not data['leaderboard']:out+='<tr><td colspan="7">Aucun modèle encore comparé.</td></tr>'
    out+='</tbody></table></div><p class="notice">'+e(data['warning'])+'</p></section>'
    out+='<section class="card"><h2>Ajouter un cas de référence</h2><p>Utilisez uniquement des faits fictifs ou irréversiblement anonymisés. Les champs attendus servent au calcul déterministe des scores.</p>'
    fields='''<div class="eval410-grid"><label>Nom du cas<input required name="name" maxlength="160" placeholder="Courriel contentieux — dates et arguments"></label><label>Famille<select name="task_kind"><option value="mixed">Cas mixte</option><option value="matter_linking">Rattachement au dossier</option><option value="dates">Dates</option><option value="citations">Citations</option><option value="adverse_arguments">Arguments adverses</option><option value="pieces">Pièces</option><option value="mail_draft">Projet de courriel</option><option value="word_document">Respect Word</option></select></label></div><label>Cas anonymisé<textarea required name="prompt" rows="8" maxlength="30000" placeholder="Dossier fictif D-001…"></textarea></label><div class="eval410-grid"><label>Dossier attendu<input name="matter_id" maxlength="120"></label><label>Dates attendues, une par ligne<textarea name="dates" rows="3"></textarea></label><label>Citations attendues<textarea name="citations" rows="3"></textarea></label><label>Arguments adverses attendus<textarea name="adverse_arguments" rows="3"></textarea></label><label>Pièces attendues<textarea name="pieces" rows="3"></textarea></label><label>Marqueurs du courriel<textarea name="mail_required" rows="3"></textarea></label><label>Formulations interdites<textarea name="mail_forbidden" rows="3"></textarea></label><label>Marqueurs du modèle Word<textarea name="word_markers" rows="3"></textarea></label><label>Affirmations factuelles permises<textarea name="permitted_claims" rows="3"></textarea></label></div><label class="check"><input required type="checkbox" name="anonymized" value="yes"> Je confirme que ce cas ne contient aucune donnée réelle ou réidentifiante.</label>'''
    out+=form('save_legal_benchmark_case410','Enregistrer le cas',extra=fields)+'</section>'
    out+='<section class="card"><h2>Comparer les modèles</h2><p>Une ligne par cible sous la forme <code>fournisseur:modèle</code>. Pour Ollama : <code>ollama:qwen3.8:27b</code>. Un fournisseur externe consomme le budget configuré.</p><p>Fournisseurs disponibles : '+e(', '.join(x['id']+' ('+x['model']+')' for x in providers if x['enabled']))+'</p>'
    out+=form('run_legal_benchmark410','Lancer la comparaison',extra='<label>Modèles à comparer<textarea required name="models" rows="5" maxlength="3000" placeholder="ollama:qwen3.8:27b\nopenrouter:openai/gpt-5"></textarea></label><label class="check"><input required type="checkbox" name="confirm_cost" value="yes"> Je confirme le lancement et, le cas échéant, le coût externe dans les plafonds configurés.</label>')+'</section>'
    out+='<section class="card"><h2>Cas enregistrés</h2><div class="eval410-cases">'
    for item in data['cases']:
        out+='<article><small>'+e(item['task_kind'])+' · '+('anonymisé' if item['anonymized'] else 'bloqué')+'</small><h3>'+e(item['name'])+'</h3><p>'+str(sum(len(v) if isinstance(v,list) else bool(v) for v in item['expected'].values()))+' critère(s) attendu(s)</p></article>'
    if not data['cases']:out+='<p class="empty">Ajoutez au moins un cas anonymisé avant de comparer les modèles.</p>'
    return out+'</div></section>'
