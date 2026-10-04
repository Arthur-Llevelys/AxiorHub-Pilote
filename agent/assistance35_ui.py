"""Server-rendered supervised practice tools, with explicit source coverage."""
import json

from .assistance35 import comparables, preview, recent
from .common import Stop


def page(desk,e,link,form,args):
    matter=str(args.get('matter',''))
    out='<section class="daily-hero"><div><p class="eyebrow">VERSION 3.5.0 · ASSISTANCE MÉTIER</p><h2>Préparer, comparer et contrôler</h2><p>Plaidoirie et coaching, simulations contradictoires sourcées, appels, calculs et facturation à relire. Aucun appel téléphonique, dépôt, facture ou envoi n’est effectué ici.</p></div></section>'
    out+='<section><h2>Plaidoirie et coaching</h2><p>'+link('/audiences-word','Préparer la plaidoirie et ses plans 5, 10 et 20 minutes')+'</p>'
    out+=form('coach_hearing35','Évaluer mon entraînement',extra='<label>Identifiant du projet d’audience validable<input required name="hearing_project_id" pattern="[a-f0-9]{32}" maxlength="32"></label><label>Transcription relue de l’entraînement<textarea required name="speech" maxlength="20000" rows="5"></textarea></label><label>Durée réelle en secondes<input type="number" required name="duration_seconds" min="30" max="7200"></label><label>Plan ciblé<select name="target_minutes"><option value="5">5 minutes</option><option value="10">10 minutes</option><option value="20">20 minutes</option></select></label>')
    out+='<p class="notice">La couverture en % est un repérage lexical du plan ; elle ne mesure ni la qualité juridique ni une chance de succès.</p></section>'
    out+='<section><h2>Simulations contradictoires et jurisprudence</h2><p>'+link('/orchestrateur-avis','Rédiger un avis contradictoire et examiner les deux thèses')+' · '+link('/recherche-juridique','Préparer une requête MCP anonymisée et importer ses résultats')+'</p>'
    out+='<p>Seules les décisions vérifiées sur un texte officiel sont citables. Les résultats OpenLegi, GoodLegal et Pappers sont des pistes tant qu’ils ne sont pas contrôlés. La fréquence descriptive des décisions comparables exige dix décisions qualifiées par l’avocat ; elle ne prédit pas le jugement.</p>'
    if matter:
        try:
            data=comparables(desk,matter)
            out+='<p>Décisions favorables / décisions distinctes comparables : '+str(data['favorable_count'])+' / '+str(data['comparable_count'])+' ; fréquence favorable observée : '+(str(data['observed_favorable_percent'])+' %' if data['observed_favorable_percent'] is not None else 'série insuffisante (moins de dix décisions)')+'.</p>'
            for r in data['labeled_decisions'][:20]:
                out+='<p>'+e(r['identifier'])+' · '+e(r['outcome'])+' · '+e(r['similarity_reason'])+' · <a rel="noopener noreferrer" target="_blank" href="'+e(r['official_url'])+'">Texte officiel</a></p>'
            out+='<h3>Qualifier une décision officielle vérifiée</h3>'
            for row in desk.db.execute("SELECT id,identifier,official_url FROM legal_authorities_v240 WHERE matter=? AND verification_status='verified' ORDER BY retrieved DESC LIMIT 20",(data['matter'],)):
                out+=form('classify_comparable35','Enregistrer la comparaison',{'matter':data['matter'],'authority_id':row['id']},
                    '<p>'+e(row['identifier'])+' · <a rel="noopener noreferrer" target="_blank" href="'+e(row['official_url'])+'">Voir la source</a></p><label>Issue qualifiée<select name="outcome"><option value="excluded">Non comparable</option><option value="favorable">Favorable</option><option value="unfavorable">Défavorable</option><option value="mixed">Mixte</option></select></label><label>Similarités et différences de faits ou de droit<textarea required name="similarity_reason" minlength="20" maxlength="1500"></textarea></label><label>Juridiction, procédure et stade<input required name="procedural_context" maxlength="300"></label>')
        except Stop:out+='<p class="notice">Dossier absent ou ambigu ; saisir sa référence exacte.</p>'
    out+='</section><section><h2>Appels préparés</h2>'
    out+=form('prepare_call35','Préparer la fiche d’appel',extra='<label>Référence exacte du dossier<input required name="matter" maxlength="80" value="'+e(matter)+'"></label><label>Objet de l’appel<input required name="purpose" maxlength="1000"></label><label>Courriel du correspondant lié au dossier (si connu)<input type="email" name="contact_email" maxlength="254"></label>')
    out+='<p>La fiche prépare les questions et les documents à relire. Elle ne compose aucun numéro et n’enregistre pas de conversation.</p></section>'
    out+='<section><h2>Calculs contrôlables</h2><p>Indiquer une source officielle et sa référence précise. Les paramètres saisis et la formule sont conservés ; le taux, l’indice et leur applicabilité restent à vérifier par l’avocat.</p>'
    common='<label>Référence exacte du dossier<input required name="matter" maxlength="80" value="'+e(matter)+'"></label><label>URL HTTPS officielle<input required type="url" name="source_url" maxlength="1500"></label><label>Référence précise du taux, de l’indice ou de la règle<input required name="source_reference" maxlength="300"></label>'
    out+=form('calculate35','Calculer les intérêts simples',{'calculation_type':'simple_interest'},common+'<label>Principal en euros<input required name="principal" inputmode="decimal"></label><label>Taux annuel en %<input required name="annual_rate" inputmode="decimal"></label><label>Date de début<input required type="date" name="start_date"></label><label>Date de fin exclue<input required type="date" name="end_date"></label>')
    out+=form('calculate35','Calculer une indexation',{'calculation_type':'rent_indexation'},common+'<label>Loyer de base en euros<input required name="rent" inputmode="decimal"></label><label>Indice de base<input required name="base_index" inputmode="decimal"></label><label>Nouvel indice<input required name="new_index" inputmode="decimal"></label>')
    out+=form('calculate35','Additionner des jours calendaires',{'calculation_type':'calendar_days'},common+'<label>Date de départ<input required type="date" name="start_date"></label><label>Nombre de jours<input required name="days" type="number" min="1" max="3650"></label>')
    out+='<p class="notice">Le dernier calcul est une addition de dates. Il ne détermine aucun délai de procédure ni report de jour férié.</p></section>'
    out+='<section><h2>Facturation préparée</h2><p>Lecture du cache Invoice Ninja après synchronisation, rattachement explicite du client et contrôle de la fraîcheur. Aucun montant de facturation ne découle automatiquement d’un temps estimé.</p>'
    out+=form('refresh_unpaid_invoices','Synchroniser les impayés')
    out+=form('billing_review35','Préparer la revue de facturation',extra='<label>Référence exacte du dossier<input required name="matter" maxlength="80" value="'+e(matter)+'"></label>')
    out+='</section><section><h2>Projets internes récents</h2>'
    try:projects=recent(desk,matter=matter,limit=25)
    except Stop:projects=[]
    for row in projects:
        out+='<article class="proposal-row"><div><small>'+e(row['created'])+' · '+e(row['kind'])+'</small><h3>'+e(row['matter'])+' · '+e(row['status'])+'</h3></div>'+link('/assistance-metier','Examiner',project=row['project_id'],matter=row['matter'])+'</article>'
    project=str(args.get('project',''))
    if args.get('job'):
        try:
            job=desk.db.execute('SELECT result FROM jobs WHERE id=?',(int(args['job']),)).fetchone()
            if job and job['result']:
                result=json.loads(job['result']);project=result.get('project_id',project)
        except (ValueError,TypeError):pass
    if project:
        try:
            shown=preview(desk,project)
            out+='<article class="project-preview"><h3>'+e(shown['kind'])+' · '+e(shown['matter'])+'</h3><pre>'+e(json.dumps(shown,ensure_ascii=False,indent=2)[:30000])+'</pre></article>'
            if shown['kind']=='appel':
                out+=form('record_call35','Enregistrer des notes à relire',{'call_project_id':shown['project_id']},'<label>Notes de l’appel réel, saisies après l’échange<textarea required name="notes" maxlength="12000" rows="5"></textarea></label>')
        except Stop:out+='<p class="notice">Projet introuvable.</p>'
    return out+'</section>'
