"""Read-only presentation of scheduled work, with an explicit manual refresh."""
from html import escape
import json

from .proactive34 import latest


def page(desk,e,link,form):
    data=latest(desk);cycle=data['cycle'];brief=data['briefing']
    out='<section class="daily-hero"><div><p class="eyebrow">VERSION 3.4.0 · PRÉPARATION PROACTIVE</p><h2>Le prochain travail est déjà repéré</h2><p>Analyse des dossiers actifs toutes les quatre heures, briefing quotidien à 7 h 30 (Paris) et préparation des audiences de plaidoirie dans les quatorze prochains jours. Les propositions restent à examiner.</p></div><div class="actions">'+form('proactive34_now','↻ Analyser maintenant')+'</div></section>'
    out+='<section class="note"><h2>Couverture des sources</h2>'
    if not data['enabled']:out+='<p class="notice">Automatisme en pause dans Paramètres.</p>'
    if cycle:
        out+='<p>Dernier passage : <strong>'+e(cycle['started'])+'</strong> · état : <strong>'+e(cycle['state'])+'</strong></p>'
        try:report=json.loads(cycle['data'])
        except ValueError:report={}
        if report.get('baseline'):out+='<p>Premier passage de référence : les sources existantes ne sont pas traitées comme des nouveautés.</p>'
        for warning in report.get('warnings',[]):out+='<p class="notice">'+e(str(warning))+'</p>'
    else:out+='<p class="notice">Aucun passage achevé. Les nouveautés ne sont pas encore établies.</p>'
    out+='</section><section><h2>Briefing du matin</h2>'
    if brief:
        out+='<p><strong>'+e(brief['day'])+'</strong> · '+str(brief['needs_action'])+' courriel(s) à examiner · '+str(brief['ready_drafts'])+' brouillon(s) à relire</p>'
        for warning in brief['coverage_warnings']:out+='<p class="notice">'+e(str(warning))+'</p>'
        if not brief['agenda_next_14_days']:out+='<p>Aucun événement dans le cache actuel. Vérifier la couverture de l’agenda ci-dessus.</p>'
        for event in brief['agenda_next_14_days']:
            mid=event.get('matter','');out+='<article class="proposal-row"><div><small>'+e(event.get('starts',''))+'</small><h3>'+e(event.get('title',''))+'</h3></div>'+ (link('/matter','Ouvrir le dossier',id=mid) if mid else link('/agenda','Ouvrir l’agenda'))+'</article>'
    else:out+='<p class="notice">Le premier briefing sera publié après 7 h 30 (heure de Paris), si le worker fonctionne.</p>'
    out+='</section><section><h2>Nouveautés et audiences à préparer</h2>'
    if not data['notices']:out+='<p>Aucune nouveauté détectée depuis le premier passage.</p>'
    group=None
    for item in data['notices']:
        key=(item['slot'],item['matter'])
        if group!=key:
            group=key;out+='<h3>Passage '+e(item['slot'])+' · dossier '+e(item['matter'] or 'à identifier')+'</h3>'
        out+='<article class="proposal-row"><div><small>'+e(item['created'])+' · '+e(item['category'])+' · '+e(item['state'])+'</small><h3>'+e(item['title'])+'</h3><p>'+e(item['detail'])+'</p><p>'+e(item['action'])+'</p></div><div class="actions">'
        if item['matter']:out+=link('/matter','Ouvrir le dossier',id=item['matter'])
        if item['category']=='mail':out+=link('/mail','Voir le courriel',key=item['source_id'])
        if item['category'].startswith('hearing'):out+=link('/audiences-word','Voir les audiences',matter=item['matter'])
        if item['job_id']:
            row=desk.db.execute('SELECT status,result FROM jobs WHERE id=?',(item['job_id'],)).fetchone()
            out+='<span>Préparation n° '+str(item['job_id'])+' : '+e(row['status'] if row else 'introuvable')+'</span>'
            if row and row['status']=='error':
                try:reason=json.loads(row['result'] or '{}').get('erreur','Vérifier le journal du worker.')
                except ValueError:reason='Vérifier le journal du worker.'
                out+='<p class="notice">'+e(str(reason))+'</p>'
        out+='</div></article>'
    return out+'</section>'
