"""Human-scale entry point. Summaries are grounded in local records, not invented."""
from html import escape as e
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from .guide import catalogue


def home(desk,form,link,url):
    from .integration import dashboard
    from .improvements36 import matter_option
    from .common import load_matters
    from .workstation import daily_briefing, external_links, why_nothing
    info=dashboard(desk);counts=info['counts'];tz=ZoneInfo(desk.c.get('calendar',{}).get('timezone','Europe/Paris'))
    briefing=daily_briefing(desk);outside=external_links(desk);why=why_nothing(desk)
    def external(key,label,icon):
        target=outside.get(key,'')
        if not target:return '<a class="external-unconfigured" href="'+e(url('/administration'),quote=True)+'" title="Renseigner l’adresse dans Poste de travail et raccourcis">'+icon+' '+e(label)+' · Configurer</a>'
        return '<a href="'+e(target,quote=True)+'" target="_blank" rel="noopener noreferrer">'+icon+' '+e(label)+'</a>'
    now=datetime.now(timezone.utc);today=now.astimezone(tz).date()
    events=[]
    for row in desk.db.execute('SELECT id,title,starts,ends FROM calendar_cache ORDER BY starts'):
        try:a=datetime.fromisoformat(row['starts']);b=datetime.fromisoformat(row['ends'])
        except ValueError:continue
        if a.tzinfo and b.tzinfo and b>now and a<now+timedelta(days=7):events.append(dict(row))
    matters=load_matters(desk.c)
    options='<option value="">Tout le cabinet — choisir un dossier pour une demande ciblée</option>'+''.join('<option value="'+e(m['id'],quote=True)+'">'+e(matter_option(m))+'</option>' for m in matters)
    out='<nav class="workstation-bar" aria-label="Applications du cabinet">'+external('roundcube','Roundcube','📬')+external('roundcube_drafts','Mes brouillons','📝')+external('openwebui','Open WebUI','🤖')+external('nextcloud','Nextcloud / OnlyOffice','📁')+external('invoice_ninja','Invoice Ninja','💶')+'</nav>'
    out+='<section class="welcome"><p class="eyebrow">POSTE DE TRAVAIL AVOCAT · '+e(today.strftime('%d/%m/%Y'))+'</p><h2>Bonjour Maître. Par quoi commençons-nous ?</h2><p>'+str(counts.get('needs_action',0))+' courriel(s) à traiter, '+str(counts.get('needs_confirmation',0))+' choix à vérifier et '+str(len(events))+' événement(s) connus dans les sept prochains jours.</p><small>Briefing fondé sur les dernières synchronisations locales ; une absence n’est jamais présentée comme une certitude.</small></section>'
    out+='<section><h2>Briefing quotidien</h2><div class="briefing-grid">'
    for value,label in ((briefing['events_next_7_days'],'Événements à 7 jours'),(briefing['new_mail_24h'],'Nouveaux courriels 24 h'),(briefing['open_anomalies'],'Anomalies ouvertes'),(briefing['ready_projects'],'Projets prêts'),(briefing['decisions_expected'],'Décisions attendues'),(briefing['unpaid_invoices'] if briefing['unpaid_synchronized'] else '—','Factures impayées' if briefing['unpaid_synchronized'] else 'Factures · non synchronisées')):
        out+='<article><strong>'+str(value)+'</strong><span>'+e(label)+'</span></article>'
    out+='</div>'
    if briefing['unread_notifications'] or briefing['ready_projects']:
        out+='<article class="notification-ready"><h3>🔔 Travail prêt à contrôler</h3><p>'+str(briefing['unread_notifications'])+' notification(s) non lue(s) et '+str(briefing['ready_projects'])+' projet(s) documentaire(s) prêt(s).</p>'+link('/orchestrateur-avis','Voir les notifications')+' · '+link('/projets','Contrôler les projets')+'</article>'
    out+='</section>'
    out+='<div class="focus-grid"><article><span class="focus-icon">📅</span><h3>À venir</h3>'
    for event in events[:3]:
        when=datetime.fromisoformat(event['starts']).astimezone(tz)
        out+='<p><strong>'+e(when.strftime('%d/%m %H:%M'))+'</strong> '+e(event['title'][:140])+'</p>'
    if not events:out+='<p>Aucun événement dans le cache pour cette période. Cela ne prouve pas une absence de rendez-vous.</p>'
    out+=link('/planning','Voir mon agenda')+'</article><article><span class="focus-icon">📬</span><h3>Mes courriels</h3><p>'+str(counts.get('needs_action',0))+' à examiner · '+str(counts.get('draft_ready',0))+' brouillon(s) prêt(s).</p>'+external('roundcube','Ouvrir Roundcube','')+' · '+external('roundcube_drafts','Ouvrir directement Brouillons','')+'</article><article><span class="focus-icon">✋</span><h3>À décider</h3><p>Consulter les propositions et leurs conséquences avant de confirmer.</p>'+link('/pilotage','Examiner les décisions')+' · '+link('/projets','Voir les projets')+'</article></div>'
    orchestrator=desk.c.get('orchestrator',{})
    out+='<section class="autonomy-switches"><div><p class="eyebrow">AUTONOMIE CONTRÔLÉE</p><h2>Ce qu’AxiorHub prépare en arrière-plan</h2><p>Mettre en pause empêche les nouveaux déclenchements. Les éléments déjà préparés restent disponibles et aucun courriel n’est envoyé.</p></div><div class="switch-grid">'
    for key,title,description in (
        ('automatic_mail_drafts_enabled','Brouillons de courriels',
         'Dépose uniquement les réponses vérifiées dans le dossier Brouillons IMAP configuré.'),
        ('automatic_legal_projects_enabled','Projets d’actes juridiques',
         'Prépare une prévisualisation interne ; aucun fichier Nextcloud sans confirmation.')):
        active=desk.settings('automation:'+key,orchestrator.get(key,True))
        out+='<article class="automation-card"><span class="status-pill '+('on' if active else 'off')+'">'+('ACTIF' if active else 'EN PAUSE')+'</span><h3>'+e(title)+'</h3><p>'+e(description)+'</p>'+form('automation_setting','Mettre en pause' if active else 'Reprendre',{'key':key,'value':'no' if active else 'yes','back':'home'})+'</article>'
    out+='</div></section>'
    out+='<details class="tutorial why-panel"><summary>Pourquoi rien n’a été produit ?</summary><p>'+str(why['verified_drafts_24h'])+' brouillon(s) déposés et relus dans les dernières 24 h · '+str(why['uncertain_drafts'])+' dépôt(s) incertain(s) à vérifier dans Roundcube. Les UID récents, lus ou non lus, sont examinés une fois.</p><div class="why-reasons">'
    for item in why['reasons'][:8]:out+='<p><strong>'+str(item['count'])+' ×</strong> '+e(item['label'])+'</p>'
    if not why['reasons']:out+='<p>Aucun motif d’arrêt n’est encore enregistré.</p>'
    if why['recent_mail_issues']:
        out+='<h3>Courriels bloqués récemment</h3>'
        for item in why['recent_mail_issues']:
            out+='<p>'+link('/mail','Voir le courriel',key=item['key'])+' — '+e(item['label'])+' <small>'+e(item['updated'])+'</small></p>'
    if why.get('recent_failures'):
        out+='<h3>Derniers traitements en échec</h3><p>Historique cumulé ; les dates permettent de distinguer les échecs anciens.</p>'
        for item in why['recent_failures']:
            out+='<p><strong>Opération '+str(item['id'])+' · '+e(item['kind'])+'</strong> — '+e(item['label'])+' <small>'+e(str(item['finished'] or item['created']))+'</small></p>'
    out+='</div><p><strong>Brouillons :</strong> '+('actifs' if why['mail_automation_active'] else 'en pause')+' · <strong>Actes :</strong> '+('actifs' if why['document_automation_active'] else 'en pause')+' · <strong>Reprise rétroactive :</strong> '+str(why['mail_collection']['retroactive_days'])+' jours, '+str(why['mail_collection']['candidate_limit'])+' UID maximum par passage.</p>'+form('run','Analyser maintenant')+' '+link('/administration','Voir les traitements')+'</details>'
    out+='<section class="conversation-box"><h2>Que souhaitez-vous préparer ?</h2><p>Choisissez le dossier, puis écrivez ou dictez. La dictée remplit le champ : elle ne valide ni n’envoie la demande.</p>'+form('assistant_ask','Demander une analyse interne',extra='<label>Dossier<select name="matter">'+options+'</select></label><label>Votre demande<textarea id="guided-question" name="question" required maxlength="12000" rows="4" placeholder="Résume ce dossier, indique les nouveautés et propose les trois prochaines actions…"></textarea></label>')+'<p class="muted">Pour lancer un projet de document, ouvrez le parcours correspondant ci-dessous. L’assistant interne répond aux questions ; les formulaires spécialisés déclenchent les préparations.</p></section>'
    out+='<section><h2>Vos six parcours</h2><p>Ouvrir un parcours ne lance aucune action. « Copier » prépare un prompt pour Open WebUI.</p><div class="journey-grid">'
    for card in catalogue()['journeys']:
        out+='<article class="journey"><span class="focus-icon">'+card['icon']+'</span><h3>'+e(card['title'])+'</h3><p>'+e(card['summary'])+'</p>'+link(card['href'],'Ouvrir')+' <button type="button" class="secondary copy-prompt" data-prompt="'+e(card['prompt'],quote=True)+'">Copier le prompt</button></article>'
    out+='</div><p id="clipboard-status" role="status"></p></section>'
    scans=desk.db.execute("SELECT COUNT(*) FROM settings WHERE key LIKE 'watch_scan_v300:%'").fetchone()[0]
    if scans:out+='<p class="notice">'+str(scans)+' inventaire(s) de dossier en cours par lots. Les données antérieures restent disponibles ; les conclusions d’exhaustivité attendent la fin du parcours.</p>'
    out+='<details class="tutorial"><summary>Guide rapide : comprendre les actions et les automatismes</summary><h3>Analyser → Prévisualiser → Confirmer</h3><p>1. Choisissez la référence exacte du dossier. 2. Préparez un projet. 3. Contrôlez les sources, les incertitudes et les futurs fichiers. 4. Confirmez la source et la destination. 5. AxiorHub crée une nouvelle version sans écrasement. 6. Le bouton « Modifier dans OnlyOffice » ouvre le fichier depuis Nextcloud ; OnlyOffice l’enregistre dans ce même stockage.</p><h3>Les boutons du pilotage</h3><p><strong>Prendre connaissance</strong> clôt l’alerte localement. <strong>Me le rappeler dans 24 h</strong> masque cette alerte 24 h ; aucune date ni audience n’est déplacée. <strong>Écarter cette proposition</strong> la retire de la file ; aucun fichier ni courriel n’est supprimé.</p><h3>Les automatismes</h3><p>Les collectes et propositions existantes conservent vos réglages. Un raccourci n’active pas silencieusement un automatisme.</p>'+link('/surveillance','Voir les réglages de surveillance')+' · '+link('/administration','Voir les traitements et connexions')+'<h3 id="audio">Réunion et dictée</h3><p>Le mode <strong>Rapide</strong> utilise la reconnaissance du navigateur et doit rester réservé au texte non confidentiel. Le mode <strong>Confidentiel</strong> envoie l’audio à Vocal local. Le microphone ne démarre jamais automatiquement. Aucun import SpeakR automatique : vous choisissez toujours l’enregistrement et le dossier.</p><a href="https://speakr.example.com/" target="_blank" rel="noopener noreferrer">Ouvrir SpeakR</a></details>'
    return out
