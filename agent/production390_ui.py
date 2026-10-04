"""Lawyer-facing production dashboard for AxiorHub 3.9.0."""
import html

from .production390 import dashboard


def e(value):return html.escape(str(value or ''),quote=True)


def page(desk,form,link):
    data=dashboard(desk);c=data['counts']
    out='<section class="prod-hero"><div><p class="eyebrow">VERSION 3.9.0 · PRODUCTION AUTONOME CONTRÔLÉE</p><h1>Production</h1><p>AxiorHub prépare automatiquement ce qui est interne et réversible. Envoi, signature, dépôt RPVA, paiement et écrasement restent bloqués jusqu’à votre décision.</p></div>'+form('production_cycle390','Lancer la production maintenant',{'limit':'20'})+'</section>'
    out+='<section class="prod-kpis">'
    for value,label in ((c['verified_mail_drafts'],'Brouillons IMAP vérifiés'),(str(data['mail_draft_coverage_percent'])+' %','Couverture des réponses'),(c['prepared_projects'],'Projets préparés'),(c['delivered_outputs'],'Livrables créés'),(c['decision_required'],'Décisions réellement requises'),(c['errors'],'Erreurs à corriger')):
        out+='<article><strong>'+e(value)+'</strong><span>'+e(label)+'</span></article>'
    out+='</section><section class="card"><div class="section-heading"><div><h2>Livrables récents</h2><p>Un résultat doit être visible, relié au dossier et accompagné de son état.</p></div>'+form('advance_playbooks390','Continuer les playbooks',{'limit':'30'})+'</div>'
    if not data['recent_outputs']:out+='<p class="empty">Aucun livrable 3.9.0 enregistré pour le moment.</p>'
    else:
        out+='<div class="prod-output-list">'
        for item in data['recent_outputs']:
            out+='<article class="prod-'+e(item['status'])+'"><div><small>'+e(item['updated'])+' · '+e(item['matter'] or 'Cabinet')+'</small><h3>'+e(item['label'])+'</h3><p>'+e(item['status'].replace('_',' '))+'</p>'
            if item['paths']:out+='<ul>'+''.join('<li>'+e(path)+'</li>' for path in item['paths'])+'</ul>'
            out+='</div>'+(link('/matter','Ouvrir le dossier',id=item['matter']) if item['matter'] else '')+'</article>'
        out+='</div>'
    out+='</section><section class="card"><h2>Erreurs de production</h2>'
    if not data['errors_by_job']:out+='<p>Aucune erreur de production sur la période.</p>'
    else:out+='<table><thead><tr><th>Traitement</th><th>Nombre</th><th>Dernière erreur</th></tr></thead><tbody>'+''.join('<tr><td>'+e(x['job_kind'])+'</td><td>'+e(x['count'])+'</td><td>'+e(x['last_seen'])+'</td></tr>' for x in data['errors_by_job'])+'</tbody></table>'
    out+='</section><section class="prod-boundary"><h2>Frontière d’autonomie</h2><div><span>Automatique</span><p>Analyser, indexer, préparer les brouillons, créer de nouveaux fichiers dans les dossiers AxiorHub_Brouillons et poursuivre les playbooks.</p></div><div><span>Validation obligatoire</span><p>Envoyer, signer, déposer au RPVA, payer, facturer définitivement, supprimer ou écraser une source.</p></div></section>'
    return out
