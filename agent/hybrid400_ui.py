"""Console avocat du routage local-first AxiorHub 4.0."""
from html import escape
from .ai_gateway import PURPOSES, public_providers, usage_snapshot
from .hybrid400 import snapshot, take_preview

def e(value): return escape(str(value if value is not None else ''), quote=True)

REASONS = {
 'mode_local': 'Mode local imposé', 'mode_manual': 'Routage manuel 3.9 conservé',
 'fonction_locale': 'Fonction non autorisée à sortir du serveur',
 'autorisation_externe_absente': 'Autorisation externe absente',
 'taille_externe_depassee': 'Charge trop volumineuse pour la politique externe',
 'openrouter_indisponible': 'OpenRouter inactif ou absent',
 'donnees_externes_non_autorisees': 'Transmission externe non autorisée',
 'zdr_non_exige': 'ZDR non exigé dans la configuration',
 'anonymisation_desactivee': 'Anonymisation obligatoire désactivée',
 'complexite_sous_seuil': 'Complexité sous le seuil',
 'modele_openrouter_absent': 'Modèle OpenRouter absent',
 'complexite_au_dessus_seuil': 'Tâche complexe : OpenRouter sélectionné',
 'dossier_exclu': 'Dossier interdit de transmission externe',
 'plafond_requete': 'Plafond par requête atteint',
 'budget_mensuel': 'Budget mensuel atteint',
 'echec_local_eligible': 'Échec local éligible à une reprise externe',
}


def page(desk, args, form):
    data = snapshot(desk); rule = data['policy']
    providers = {x['id']: x for x in public_providers(desk.c)}
    external = providers.get(rule['external_provider'], {})
    usage = usage_snapshot(desk, rule['external_provider'])
    spent = sum(float(x.get('cost_usd', 0) or 0) for x in usage)
    external_count = sum(1 for x in data['decisions'] if x['external'] and x['status'] in ('external_completed', 'external_after_local_failure'))
    local_count = sum(1 for x in data['decisions'] if not x['external'] and x['status'] == 'local_selected')
    fallback_count = sum(1 for x in data['decisions'] if x['status'] in ('local_fallback', 'external_after_local_failure'))
    mode_label = {'local': 'Local uniquement', 'hybrid': 'Hybride local/OpenRouter',
                  'manual': 'Manuel par fonction'}[rule['mode']]
    out = '<section class="hybrid400-hero"><div><p class="eyebrow">VERSION 4.0.0 · ROUTAGE HYBRIDE</p><h1>Le local d’abord, l’externe seulement quand il apporte quelque chose</h1><p>Chaque choix est estimé avant exécution, expliqué et journalisé sans conserver la demande ni la réponse.</p></div><span class="hybrid400-mode">'+e(mode_label)+'</span></section>'
    out += '<section class="hybrid400-kpis"><article><strong>'+str(local_count)+'</strong><span>Décisions locales</span></article><article><strong>'+str(external_count)+'</strong><span>Traitements OpenRouter</span></article><article><strong>'+str(fallback_count)+'</strong><span>Reprises et replis</span></article><article><strong>'+e(round(spent, 4))+' $</strong><span>Dépense externe ce mois</span></article></section>'
    out += '<section class="card hybrid400-flow"><h2>Décision avant tout envoi</h2><ol><li><strong>Dossier et fonction</strong><span>Un dossier exclu ou une fonction non autorisée reste local.</span></li><li><strong>Score de complexité</strong><span>Volume, sources et nature du travail sont évalués sans modèle.</span></li><li><strong>Anonymisation et budget</strong><span>Les identités sont masquées et les deux plafonds sont vérifiés.</span></li><li><strong>Contrôle</strong><span>Le second modèle peut contredire le premier sans être le même couple fournisseur/modèle.</span></li></ol></section>'
    mode_options = ''.join('<option value="'+key+'"'+(' selected' if rule['mode'] == key else '')+'>'+label+'</option>' for key, label in [('local', 'Local uniquement'), ('hybrid', 'Hybride automatique'), ('manual', 'Manuel par fonction (compatibilité 3.9)')])
    provider_options = ''.join('<option value="'+e(x['id'])+'"'+(' selected' if x['id'] == rule['external_provider'] else '')+'>'+e(x['id']+' · '+x['model'])+'</option>' for x in providers.values() if x['type'] == 'openrouter') or '<option value="openrouter">OpenRouter à configurer</option>'
    checks = '<fieldset><legend>Fonctions autorisées à utiliser OpenRouter</legend>'
    for key, (label, role) in PURPOSES.items():
        checks += '<label class="ws-check"><input type="checkbox" name="purposes" value="'+e(key)+'"'+(' checked' if key in rule['allowed_purposes'] else '')+'> '+e(label)+'</label>'
    checks += '</fieldset>'
    excluded = '\n'.join(rule['excluded_matters'])
    extra = '<label>Mode<select name="mode">'+mode_options+'</select></label><label>Fournisseur externe<select name="external_provider">'+provider_options+'</select></label><div class="hybrid400-two"><label>Seuil de complexité sur 100<input type="number" name="threshold" min="20" max="95" value="'+str(rule['threshold'])+'"></label><label>Taille externe maximale, caractères<input type="number" name="max_external_characters" min="4000" max="250000" value="'+str(rule['max_external_characters'])+'"></label></div>'+checks+'<label>Dossiers toujours locaux — un identifiant exact par ligne<textarea name="excluded_matters" rows="6" placeholder="2018091201">'+e(excluded)+'</textarea></label><input type="hidden" name="anonymize_external" value="yes"><p class="notice">Pseudonymisation réversible obligatoire avant toute transmission (5.4.0) : elle ne peut pas être désactivée.</p><label class="ws-check"><input type="checkbox" name="escalate_after_local_failure" value="yes"'+(' checked' if rule['escalate_after_local_failure'] else '')+'> Reprendre avec OpenRouter après un échec local éligible</label><label class="ws-check"><input type="checkbox" name="fallback_local_on_error" value="yes"'+(' checked' if rule['fallback_local_on_error'] else '')+'> Revenir au local si OpenRouter échoue</label><label class="ws-check hybrid400-consent"><input type="checkbox" name="external_client_data_approved" value="yes"'+(' checked' if rule['external_client_data_approved'] else '')+'> J’autorise les seules fonctions cochées à transmettre les données anonymisées affichées dans l’aperçu, sous réserve des plafonds</label>'
    out += '<section class="card"><h2>Politique active</h2><p>Une mise à jour laisse toujours le mode local actif. Le mode hybride exige OpenRouter actif, ZDR, budgets, anonymisation et consentement explicite.</p>'+form('save_hybrid_policy400', 'Enregistrer la politique', extra=extra)+'</section>'
    state = ('Actif' if external.get('enabled') else 'Inactif')+' · '+('clé configurée' if external.get('secret_configured') else 'clé absente')+' · '+('ZDR exigé' if external.get('zdr_required') else 'ZDR non exigé')
    out += '<section class="card"><h2>OpenRouter</h2><p><strong>'+e(state)+'</strong></p><p>Modèle : '+e(external.get('model') or 'non défini')+' · plafond/requête : '+e(external.get('per_request_budget_usd', 0))+' $ · budget mensuel : '+e(external.get('monthly_budget_usd', 0))+' $ · consommé : '+e(round(spent, 6))+' $.</p><p><a href="parametres?tab=ia">Configurer la clé, le modèle et ses tarifs</a></p></section>'
    last = desk.settings('ai:hybrid:last_simulation', {})
    purpose_options = ''.join('<option value="'+e(k)+'">'+e(v[0])+'</option>' for k, v in PURPOSES.items())
    simulation = '<div class="hybrid400-two"><label>Fonction<select name="purpose">'+purpose_options+'</select></label><label>Étape<input name="stage" maxlength="80" placeholder="document_project"></label><label>Caractères estimés<input type="number" name="input_characters" min="0" max="250000" value="30000"></label><label>Réponse maximale, jetons<input type="number" name="max_tokens" min="100" max="20000" value="3500"></label><label>Sources<input type="number" name="source_count" min="0" max="1000" value="12"></label><label>Documents<input type="number" name="document_count" min="0" max="1000" value="3"></label></div>'
    out += '<section class="card"><h2>Estimer sans transmettre</h2>'+form('simulate_hybrid400', 'Calculer le routage et le coût', extra=simulation)
    if last:
        out += '<p class="'+('success' if last.get('external') else 'notice')+'"><strong>'+('OpenRouter' if last.get('external') else 'Ollama local')+'</strong> · score '+str(last.get('score', 0))+'/'+str(last.get('threshold', 0))+' · estimation '+e(round(float(last.get('estimated_cost_usd', 0) or 0), 6))+' $ · '+e(' ; '.join(REASONS.get(x, x) for x in last.get('reason_codes', [])))+'</p>'
    out += '</section>'
    matter_options = '<option value="">Aucun dossier</option>'
    try:
        from .common import load_matters
        matter_options += ''.join('<option value="'+e(x['id'])+'">'+e(x['client_name']+' — '+x['id'])+'</option>' for x in load_matters(desk.c))
    except Exception: pass
    preview_form = '<div class="hybrid400-two"><label>Fonction<select name="purpose">'+purpose_options+'</select></label><label>Dossier<select name="matter">'+matter_options+'</select></label><label>Étape<input name="stage" maxlength="80" value="chat"></label><label>Réponse maximale, jetons<input type="number" name="max_tokens" min="100" max="20000" value="3500"></label></div><label>Texte de test<textarea name="text" rows="7" maxlength="120000" required placeholder="Collez ici la demande dont vous voulez contrôler la transmission."></textarea></label>'
    out += '<section class="card"><h2>Voir avant transmission</h2><p>Cette action ne contacte aucun fournisseur. L’aperçu est anonymisé, conservé dix minutes au plus et supprimé dès son affichage.</p>'+form('preview_hybrid400', 'Afficher les données et le coût', extra=preview_form)
    shown = take_preview(desk, args.get('preview', '')) if args.get('preview') else None
    if shown:
        decision = shown['decision']; redactions = shown['redactions']
        out += '<div class="hybrid400-preview"><h3>'+('Transmission autorisée' if shown['would_transmit'] else 'Traitement maintenu en local')+'</h3><p>Modèle : '+e(decision['provider']+' · '+decision['model'])+' · estimation : '+e(round(float(decision.get('estimated_cost_usd', 0) or 0), 6))+' $ · occultations : '+e(sum(redactions.values()))+'.</p><pre>'+e(shown['transmitted_preview'])+'</pre></div>'
    out += '</section><section class="card"><h2>Dernières décisions</h2><div class="table"><table><thead><tr><th>Date</th><th>Fonction</th><th>Score</th><th>Choix</th><th>Coût estimé</th><th>État</th><th>Motifs</th></tr></thead><tbody>'
    for row in data['decisions'][:40]:
        reasons = ' ; '.join(REASONS.get(x, x.replace('_', ' ')) for x in row['reason_codes'])
        out += '<tr><td>'+e(row['at'][:19].replace('T', ' '))+'</td><td>'+e(PURPOSES.get(row['purpose'], (row['purpose'], ''))[0])+'</td><td>'+str(row['score'])+'/'+str(row['threshold'])+'</td><td>'+e(row['selected_provider']+' · '+row['selected_model'])+'</td><td>'+e(round(float(row.get('estimated_cost_usd', 0) or 0), 6))+' $</td><td>'+e(row['status'])+'</td><td>'+e(reasons)+'</td></tr>'
    if not data['decisions']: out += '<tr><td colspan="7">Aucune décision enregistrée.</td></tr>'
    out += '</tbody></table></div><p class="notice">Le journal conserve les métriques, motifs, modèles, coûts estimés et empreintes de dossiers. Il ne contient ni prompt, ni réponse, ni pièce, ni secret.</p></section>'
    return out
