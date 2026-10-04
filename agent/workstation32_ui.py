"""Server-rendered workstation pages. All dynamic text is HTML escaped."""
from datetime import datetime, timezone
from html import escape as esc

from .common import load_matters
from .desk import ROLES
from .workstation import external_links, CABINET_SITES
from .workstation32 import briefing, contacts, search, SOURCES
from .improvements36 import matter_option


def e(value):return esc(str(value if value is not None else ''),quote=True)


def home(desk,form,link):
    info=briefing(desk);matters={m['id']:m for m in load_matters(desk.c)}
    out='<section class="ws-hero"><p class="eyebrow">VOTRE CABINET · DONNÉES LOCALES</p><h2>Ce qui mérite votre attention</h2><p>'+str(info['pending_mail'])+' courriel(s) demandent une action ou une décision, '+str(info['ready_drafts'])+' brouillon(s) sont signalés prêts et '+str(len(info['events']))+' événement(s) figurent dans le cache des deux prochaines semaines.</p><div class="ws-hero-actions">'+link('/','Examiner les courriels')+link('/projets','Relire les projets')+'</div><small>Dernière trace de courriel : '+e(info['mail_last_seen'] or 'inconnue')+' · dernière synchronisation d’agenda : '+e(info['calendar_last_fetched'] or 'inconnue')+'. Ces chiffres dépendent des collectes et de leur couverture.</small></section>'
    out+='<div class="ws-cards">'
    for title,description,target,action in (
        ('Réponses à relire',str(info['pending_mail'])+' courriel(s) à examiner.','/','Ouvrir la messagerie'),
        ('Dossiers', 'Retrouver les sources et travaux par référence exacte.','/dossiers','Voir les dossiers'),
        ('Bloqués et incertains','Contrôler les échecs et les dépôts incertains.','/administration','Voir les exceptions')):
        out+='<article><h3>'+e(title)+'</h3><p>'+e(description)+'</p>'+link(target,action)+'</article>'
    out+='</div><section class="ws-command-center"><h2>Donner une directive au cabinet</h2><p>Ces commandes lancent des préparations internes : elles peuvent indexer, analyser et rédiger des projets, sans envoyer de courriel ni déposer au RPVA.</p><div class="actions">'+form('proactive34_now','Analyser les dossiers actifs')+form('orchestrator_mail_sweep','Préparer les nouveaux courriels')+form('organize_cabinet','Repérer le travail à faire')+'</div></section><section class="ws-events"><h2>Les deux prochaines semaines</h2>'
    for event in info['events']:
        out+='<div class="ws-event"><time>'+e(event['starts'][:16].replace('T',' '))+'</time><div><strong>'+e(event['title'])+'</strong><small>'+e(matters.get(event['matter'],{}).get('client_name','Dossier non relié'))+'</small></div>'
        if event['matter'] in matters:out+=link('/matter','Ouvrir le dossier',id=event['matter'])
        out+='</div>'
    if not info['events']:out+='<p>Pas d’événement dans le cache sur 14 jours. Cela ne certifie pas que l’agenda est vide.</p>'
    out+='</section><section class="ws-ask" id="directive"><h2>Travailler sur un dossier</h2><p>La demande lance une analyse interne ; aucune action externe n’est validée par ce formulaire.</p>'
    options='<option value="">Tout le cabinet</option>'+''.join('<option value="'+e(m['id'])+'">'+e(matter_option(m))+'</option>' for m in matters.values())
    out+=form('assistant_ask','Demander une analyse',extra='<label>Dossier<select name="matter">'+options+'</select></label><label>Votre demande<textarea required name="question" rows="3" maxlength="12000"></textarea></label>')+'</section>'
    out+='<details class="ws-legacy"><summary>Parcours et raccourcis complémentaires</summary>'
    # The WSGI page appends the established actions inside this details element.
    return out


def search_page(desk,args,link,url):
    q=args.get('q','').strip();source=args.get('source','all');mid=args.get('matter','')
    options={'all':'Toutes les sources','matters':'Dossiers','contacts':'Contacts','mails':'Courriels',
             'documents':'Documents','attachments':'Pièces jointes','events':'Événements'}
    if source not in SOURCES:source='all'
    matters=load_matters(desk.c)
    form='<form class="ws-search-form" method="get" action="'+e(url('/rechercher'))+'"><label>Rechercher<input name="q" value="'+e(q)+'" required minlength="2" maxlength="200" placeholder="Référence, nom, adresse, terme d’un document…"></label><label>Source<select name="source">'+''.join('<option value="'+k+'"'+(' selected' if k==source else '')+'>'+v+'</option>' for k,v in options.items())+'</select></label><label>Dossier<select name="matter"><option value="">Tous les dossiers</option>'+''.join('<option value="'+e(m['id'])+'"'+(' selected' if m['id']==mid else '')+'>'+e(matter_option(m))+'</option>' for m in matters)+'</select></label><label>Depuis<input type="date" name="after" value="'+e(args.get('after',''))+'"></label><label>Jusqu’au<input type="date" name="before" value="'+e(args.get('before',''))+'"></label><button>Rechercher</button></form>'
    out='<section class="ws-search"><h2>Recherche dans le cabinet</h2><p>Les dossiers, contacts, courriels analysés, fichiers indexés, pièces jointes extraites et événements synchronisés sont cherchés localement.</p>'+form+'</section>'
    if not q:return out+'<p class="empty">Saisissez au moins deux caractères pour chercher.</p>'
    data=search(desk,q,source,mid,args.get('after',''),args.get('before',''))
    cover=data['coverage']
    out+='<p class="ws-coverage">Périmètre connu : '+str(cover['registered_matters'])+' dossiers · '+str(cover['registered_contacts'])+' associations de contacts · '+str(cover['known_mails'])+' courriels analysés, '+str(cover['indexed_mail_sources'])+' sources de courriels indexées · '+str(cover['indexed_documents'])+' documents indexés · '+str(cover['indexed_attachments'])+' pièces jointes · '+str(cover['cached_events'])+' événements en cache. Inventaires en cours : '+str(cover['pending_inventory_scans'])+'.</p><p class="notice">'+e(data['notice'])+'</p>'
    out+='<section class="ws-results" aria-live="polite"><h2>'+str(len(data['results']))+' résultat(s)'+(' affichés, limite atteinte' if data['limited'] else '')+'</h2>'
    for item in data['results']:
        target=('/mail' if item['kind']=='Courriel' or item['kind']=='Courriel indexé' and item['key'] else '/agenda' if item['kind']=='Événement' and not item['matter'] else '/matter')
        opts={'key':item['key']} if target=='/mail' else {} if target=='/agenda' else {'id':item['matter']}
        out+='<article><span class="ws-kind">'+e(item['kind'])+'</span><h3>'+link(target,item['title'],**opts)+'</h3><small>'+e(item['matter_name']+' · '+item['matter'])+' · '+e(item['origin'])+(' · '+e(item['date'][:16]) if item['date'] else '')+'</small><p>'+e(item['excerpt'])+'</p>'
        if item['path']:out+='<small>Source : '+e(item['path'])+'</small>'
        out+='</article>'
    if not data['results']:out+='<p class="empty">Aucun résultat dans les données actuellement enregistrées ou indexées. Vérifiez la fraîcheur des sources et relancez l’indexation du dossier concerné.</p>'
    return out+'</section>'


def contacts_page(desk,args,form,link,url):
    matters=load_matters(desk.c);q=args.get('q','').strip().casefold();all_rows=contacts(matters)
    rows=[r for r in all_rows if not q or q in (r['email']+' '+r['matter_name']+' '+r['matter']).casefold()]
    out='<section class="ws-heading"><h2>Correspondants du cabinet</h2><p>Chaque ligne est une association avec un dossier précis. Une même adresse peut avoir un autre rôle dans un autre dossier ; aucune fusion par le nom.</p><small>Source : registre AxiorHub confirmé. Carnet CardDAV : connexion et synchronisation à configurer séparément ; aucune donnée CardDAV n’est inventée.</small></section>'
    out+='<form method="get" action="'+e(url('/contacts'))+'"><label>Filtrer les correspondants<input name="q" maxlength="120" value="'+e(args.get('q',''))+'" placeholder="Adresse, dossier ou référence"></label><button>Filtrer</button></form>'
    out+='<p>'+str(len(rows))+' association(s) correspondant au filtre sur '+str(len(all_rows))+' enregistrée(s).</p>'
    for row in rows[:100]:
        out+='<article class="ws-contact"><div><h3>'+e(row['email'])+'</h3><p>'+link('/matter',row['matter_name']+' · '+row['matter'],id=row['matter'])+' · '+e(ROLES.get(row['role'],row['role']))+'</p><small>'+e(row['origin'])+'</small></div><details><summary>Corriger le rôle ou retirer</summary>'+form('associate','Enregistrer le rôle',{'matter':row['matter'],'email':row['email'],'back':'contacts'},'<label>Rôle<select name="role">'+''.join('<option value="'+e(k)+'"'+(' selected' if k==row['role'] else '')+'>'+e(label)+'</option>' for k,label in ROLES.items())+'</select></label>')+form('remove_contact','Retirer de ce dossier',{'matter':row['matter'],'email':row['email'],'back':'contacts'},'<label class="ws-check"><input type="checkbox" name="confirm" value="yes" required>Confirmer le retrait de cette seule association</label>')+'</details></article>'
    if len(rows)>100:out+='<p class="notice">Affichage limité à 100 lignes. Précisez l’adresse ou la référence du dossier.</p>'
    if not rows:out+='<p class="empty">Aucune association trouvée dans le registre local.</p>'
    opts='<option value="">Choisir le dossier exact</option>'+''.join('<option value="'+e(m['id'])+'">'+e(matter_option(m))+'</option>' for m in matters)
    out+='<section><h2>Associer un correspondant à un dossier</h2>'+form('associate','Enregistrer l’association',{'back':'contacts'},'<label>Dossier<select name="matter" required>'+opts+'</select></label><label>Adresse électronique<input name="email" type="email" required maxlength="254"></label><label>Rôle<select name="role" required><option value="">Choisir</option>'+''.join('<option value="'+e(k)+'">'+e(label)+'</option>' for k,label in ROLES.items())+'</select></label>')+'</section>'
    return out


def tasks_page(desk,link,form):
    matters={m['id']:m for m in load_matters(desk.c)}
    rows=[dict(x) for x in desk.db.execute("SELECT id,matter,title,due,status FROM tasks WHERE status='open' ORDER BY due LIMIT 100")]
    external=[dict(x) for x in desk.db.execute("SELECT id,matter,title,due,status,external_uid FROM work_tasks_v211 WHERE status IN ('todo','in_progress','blocked') ORDER BY due LIMIT 100")]
    from .agenda520 import enabled as personal_enabled
    personal=personal_enabled(desk)
    external_ids={item['id'] for item in external}
    rows=[item for item in rows if item['id'] not in external_ids]
    out='<section class="ws-heading"><h2>Travail à suivre</h2><p>Tâches internes et tâches Nextcloud synchronisées, avec leur source. Une suggestion n’est pas une diligence accomplie.</p></section><div class="actions">'+link('/planning','Ouvrir l’agenda et le programme de travail')+link('/projets','Voir les diligences proposées')+'</div>'
    options='<option value="">Cabinet, sans dossier</option>'+''.join(
        '<option value="'+e(m['id'])+'">'+e(matter_option(m))+'</option>'
        for m in matters.values())
    out+='<section class="ws-heading"><h2>Ajouter une tâche</h2><p>Écrivez ou dictez la consigne, choisissez le dossier exact si elle concerne une affaire, puis relisez avant d’enregistrer. La dictée locale est disponible si elle est activée sur le serveur.</p>'
    out+=form('create_local_task','Ajouter la tâche',extra=
        '<label>Consigne<textarea name="title" required minlength="3" maxlength="2000" rows="3"></textarea></label>'
        '<label>Dossier<select name="matter">'+options+'</select></label>'
        '<label>Échéance facultative<input type="date" name="due"></label>')
    out+='</section>'
    for origin,items in [('Registre local',rows),('Cache de tâches Nextcloud',external)]:
        out+='<section><h2>'+e(origin)+' · '+str(len(items))+'</h2>'
        for row in items:
            out+='<article class="ws-result ws-task-card" id="task-'+e(row['id'])+'"><h3>'+e(row['title'])+'</h3><small>'+e(row['status'])+' · échéance '+e(row['due'] or 'non connue')+'</small><p>'
            out+=link('/matter',matter_option(matters[row['matter']]),id=row['matter']) if row['matter'] in matters else 'Dossier non relié'
            out+='</p><div class="ws-task-actions">'
            if origin=='Registre local':
                out+=form('delegate_local_task','Confier à l’IA : actualiser et préparer',{'task_id':row['id']})
                out+=form('update_local_task','Marquer terminée',{'task_id':row['id'],'status':'closed'})
                out+=form('cancel_local_task','Supprimer',{'task_id':row['id'],'confirm':'yes'})
            else:
                out+=form('assistant_ask','Demander à l’IA de préparer',
                    {'matter':row['matter'] if row['matter'] in matters else ''},
                    '<input type="hidden" name="question" value="'+e(
                      'Prépare concrètement cette tâche : '+row['title']+
                      '. Indique les sources consultées, les points incertains et les actions à faire valider.')+'">')
                own=str(row.get('external_uid','')).startswith('axiorhub-')
                status_action='update_work_task' if own else ('edit_personal_task' if personal else '')
                if status_action:
                    if row['status']!='in_progress':out+=form(status_action,'Commencer',{'task':row['id'],'status':'in_progress'})
                    out+=form(status_action,'Marquer terminée',{'task':row['id'],'status':'completed'})
                    out+=form(status_action,'Annuler',{'task':row['id'],'status':'cancelled'})
                else:
                    out+='<small>Tâche personnelle en lecture seule : activez « Modifier mes agendas et tâches personnels » dans l’onglet Agenda.</small>'
            out+='</div><details class="ws-task-editor"><summary>Modifier ou programmer</summary>'
            if origin=='Registre local':
                edit_action='update_local_task';ident={'task_id':row['id'],'status':'open'}
            else:
                edit_action='edit_work_task' if str(row.get('external_uid','')).startswith('axiorhub-') else 'edit_personal_task';ident={'task':row['id']}
            matter_select='<label>Dossier<select name="matter"><option value="">Sans dossier</option>'+''.join(
              '<option value="'+e(m['id'])+'"'+(' selected' if row['matter']==m['id'] else '')+'>'+e(matter_option(m))+'</option>' for m in matters.values())+'</select></label>' if origin=='Registre local' else ''
            out+=form(edit_action,'Enregistrer les modifications',ident,
              '<label>Intitulé<input name="title" required minlength="3" maxlength="500" value="'+e(row['title'])+'"></label>'+matter_select+
              '<label>Échéance<input type="date" name="due" value="'+e((row['due'] or '')[:10])+'"></label>')
            out+=form('schedule_work_task','Programmer dans l’agenda',{'task':row['id']},
              '<label>Début<input type="datetime-local" name="start" required></label><label>Durée (minutes)<input type="number" name="duration_minutes" value="60" min="15" max="480" step="15"></label>')
            out+='</details></article>'
        if not items:out+='<p class="empty">Aucune tâche enregistrée dans cette source.</p>'
        out+='</section>'
    return out


def settings_page(desk,args,form,link):
    tabs=[('cabinet','Cabinet et utilisateurs'),('connexions','Connexions'),('ia','IA'),('extensions','Connecteurs et extensions'),('modeles','Modèles Word'),
          ('automatismes','Automatismes'),('confidentialite','Confidentialité'),('maintenance','Maintenance')]
    selected=args.get('tab','connexions')
    if selected not in dict(tabs):selected='connexions'
    out='<nav class="ws-tabs" aria-label="Rubriques des paramètres">'+''.join(link('/parametres',label,tab=k) for k,label in tabs)+'</nav><section class="ws-settings"><h2>'+e(dict(tabs)[selected])+'</h2>'
    c=desk.c
    if selected=='cabinet':
        profile=desk.settings('cabinet:profile',{})
        out+='<p>Renseignez le style habituel de rédaction et les consignes de vérification. Ces informations sont ajoutées aux demandes adressées à l’assistant ; elles ne modifient pas le contenu d’une pièce.</p>'
        out+=form('save_cabinet_profile','Enregistrer le profil du cabinet',extra=
            '<label>Nom du cabinet<input name="name" maxlength="160" value="'+e(profile.get('name',''))+'"></label>'
            '<label>Style rédactionnel et formule de politesse<textarea name="style" maxlength="1500" rows="4">'+e(profile.get('style',''))+'</textarea></label>'
            '<label>Priorités d’analyse et règles de vérification<textarea name="guidance" maxlength="3000" rows="5">'+e(profile.get('guidance',''))+'</textarea></label>')
        out+='<p>Corrigez les noms et références depuis chaque fiche Dossier ; vérifiez les rôles dans '+link('/contacts','Contacts')+'. Les règles de tri des courriels s’ajoutent depuis le courriel à classer, avec son motif visible.</p>'
    elif selected=='connexions':
        health=desk.settings('health',{})
        out+='<p>Dernier contrôle : '+e(health.get('at') or 'non exécuté')+'. Un service non contrôlé n’est pas déclaré disponible.</p>'+form('health','Tester les connexions',{'back':'settings'})
        for name,label in [('imap','Messagerie IMAP'),('nextcloud','Nextcloud WebDAV'),('ollama','Modèle local'),('embeddings','Index sémantique')]:
            out+='<div class="ws-setting-row"><strong>'+label+'</strong><span>'+e(health.get(name,'Non contrôlé'))+'</span></div>'
        out+='<h3>Raccourcis des applications</h3><p>Les liens ouvrent un onglet. Enregistrer un lien ne configure pas le connecteur API correspondant.</p>'
        links=external_links(desk)
        for key,label in [('roundcube','Roundcube'),('roundcube_drafts','Brouillons Roundcube'),('nextcloud','Nextcloud'),('onlyoffice','OnlyOffice'),('openwebui','Open WebUI'),('invoice_ninja','Invoice Ninja')]:
            out+=form('save_external_url','Enregistrer '+label,{'key':key,'back':'settings'},'<label>'+label+' — URL HTTPS<input type="url" name="value" maxlength="1000" value="'+e(links.get(key,''))+'"></label>')
        out+='<h3>Applications juridiques du cabinet</h3>'
        for key,label,_ in CABINET_SITES:
            out+=form('save_external_url','Enregistrer '+label,{'key':key,'back':'settings'},
                '<label>'+e(label)+' — URL HTTPS<input type="url" name="value" maxlength="1000" value="'+e(links.get(key,''))+'"></label>')
        openwebui=links.get('openwebui','')
        if openwebui:out+='<p>Pour ajouter un fournisseur OpenAI ou Mistral <strong>dans Open WebUI</strong>, ouvrez <a rel="noopener noreferrer" target="_blank" href="'+e(openwebui)+'">Open WebUI</a>, puis Paramètres administrateur → Connexions. Ce réglage concerne les conversations Open WebUI et ne bascule pas le moteur local AxiorHub.</p>'
        out+='<p class="notice">Les identifiants de messagerie, Nextcloud et MCP d’AxiorHub sont gérés par le service administrateur du serveur : l’interface n’a pas les droits nécessaires pour écrire les fichiers privés et ne collecte aucun mot de passe dans une note ou un champ non protégé.</p>'
    elif selected=='ia':
        from .ai_gateway import ANTHROPIC_MODELS,PURPOSES,public_providers
        providers={row['id']:row for row in public_providers(c)}
        providers.setdefault('openai',{'id':'openai','type':'openai','url':'https://api.openai.com/v1','model':'','enabled':False,'external_data_allowed':False,'secret_configured':False})
        providers.setdefault('mistral',{'id':'mistral','type':'mistral','url':'https://api.mistral.ai/v1','model':'','enabled':False,'external_data_allowed':False,'secret_configured':False})
        providers.setdefault('openrouter',{'id':'openrouter','type':'openrouter','url':'https://openrouter.ai/api/v1','model':'','enabled':False,'external_data_allowed':False,'secret_configured':False,'zdr_required':True,'monthly_budget_usd':0,'per_request_budget_usd':0,'input_usd_per_million':0,'output_usd_per_million':0})
        providers.setdefault('anthropic',{'id':'anthropic','type':'anthropic','url':'https://api.anthropic.com/v1','model':'claude-sonnet-5-5','enabled':False,'external_data_allowed':False,'secret_configured':False,'monthly_budget_usd':0,'per_request_budget_usd':0,'input_usd_per_million':0,'output_usd_per_million':0})
        out+='<p>Le moteur commun dessert AxiorHub et le relais Roundcube. Ollama reste local ; OpenAI, Mistral, OpenRouter et Anthropic ne reçoivent des données du cabinet qu’après activation et autorisation explicites. Une clé enregistrée n’est jamais réaffichée.</p>'
        out+=('<p class="notice"><strong>Pseudonymisation obligatoire (5.4.0).</strong> Tout envoi à un fournisseur non local est pseudonymisé sur le serveur : noms, sociétés, '
              'adresses, courriels, téléphones, IBAN, numéros et références deviennent [PERSONNE_1], [SOCIETE_1]… ; la réponse est rétablie localement. '
              +link('/ia-externe','Mode d’utilisation, aperçu de ce qui part et journal')+'.</p>')
        out+='<div class="ws-provider-grid">'
        for provider_id,row in providers.items():
            health=desk.settings('ai:health:'+provider_id,{})
            out+='<article class="ws-provider"><h3>'+e(provider_id)+' · '+e(row['type'])+'</h3><p>État : <strong>'+e(health.get('status','non testé'))+'</strong>'+((' · '+e(health.get('message') or health.get('error'))) if health.get('error') else '')+'</p>'
            if row['type']=='ollama':
                out+='<p>'+e(row.get('url',''))+' · '+e(row.get('model',''))+' · traitement local.</p>'+form('test_ai_provider','Tester Ollama',{'provider_id':'ollama'})
            else:
                out+=form('save_ai_provider','Enregistrer le fournisseur',extra=
                    '<input type="hidden" name="provider_id" value="'+e(provider_id)+'"><input type="hidden" name="provider_type" value="'+e(row['type'])+'">'
                    '<label>Adresse API HTTPS<input type="url" required name="url" maxlength="500" value="'+e(row.get('url',''))+'"></label>'
                    '<label>Modèle par défaut<input required name="model" maxlength="160" value="'+e(row.get('model',''))+'"'+(' list="ax-anthropic-models"' if row['type']=='anthropic' else '')+'></label>'
                    +(('<datalist id="ax-anthropic-models">'+''.join('<option value="'+e(k)+'">'+e(v)+'</option>' for k,v in ANTHROPIC_MODELS)+'</datalist>'
                       '<p class="hint">Claude Sonnet 5.5 pour la rédaction, Haiku 4.5 pour le tri, Opus 5.5 pour les analyses lourdes.</p>') if row['type']=='anthropic' else '')+
                    '<label>Nouvelle clé API'+(' (déjà configurée, laisser vide pour la conserver)' if row.get('secret_configured') else '')+'<input type="password" name="api_key" maxlength="4096" autocomplete="new-password"></label>'
                    '<fieldset><legend>Coût et politique de données</legend><label>Plafond mensuel en USD<input type="number" min="0" max="100000" step="0.01" name="monthly_budget_usd" value="'+e(row.get('monthly_budget_usd',0))+'"></label>'
                    '<label>Plafond par requête en USD<input type="number" min="0" max="100000" step="0.001" name="per_request_budget_usd" value="'+e(row.get('per_request_budget_usd',0))+'"></label>'
                    '<label>Tarif entrée / million de jetons<input type="number" min="0" max="100000" step="0.001" name="input_usd_per_million" value="'+e(row.get('input_usd_per_million',0))+'"></label>'
                    '<label>Tarif sortie / million de jetons<input type="number" min="0" max="100000" step="0.001" name="output_usd_per_million" value="'+e(row.get('output_usd_per_million',0))+'"></label>'
                    '<label class="ws-check"><input type="checkbox" name="zdr_required" value="yes"'+(' checked' if row.get('zdr_required') else '')+'> Exiger le routage sans conservation (ZDR) lorsqu’il est pris en charge</label></fieldset>'
                    '<label class="ws-check"><input type="checkbox" name="enabled" value="yes"'+(' checked' if row.get('enabled') else '')+'> Activer ce fournisseur</label>'
                    '<label class="ws-check"><input type="checkbox" name="allow_external" value="yes"'+(' checked' if row.get('external_data_allowed') else '')+'> J’autorise l’envoi au fournisseur des données nécessaires aux fonctions qui lui seront attribuées</label>')
                if row.get('secret_configured'):out+=form('test_ai_provider','Tester la connexion',{'provider_id':provider_id})
            out+='</article>'
        from .ai_gateway import usage_snapshot
        usage=usage_snapshot(desk)
        out+='</div><h3>Consommation externe du mois</h3>'
        out+=('<p class="empty">Aucune requête externe comptabilisée ce mois.</p>' if not usage else '<div class="table"><table><thead><tr><th>Fournisseur</th><th>Requêtes</th><th>Jetons entrée</th><th>Jetons sortie</th><th>Coût estimé USD</th></tr></thead><tbody>'+''.join('<tr><td>'+e(x['provider'])+'</td><td>'+e(x['requests'])+'</td><td>'+e(x['input_tokens'])+'</td><td>'+e(x['output_tokens'])+'</td><td>'+e(x['cost_usd'])+'</td></tr>' for x in usage)+'</tbody></table></div>')
        out+='<h3>Modèle utilisé par fonction</h3><p>Le changement est testé avant activation. Pour préserver le secret professionnel, les fonctions restent sur Ollama tant qu’un fournisseur externe n’est pas expressément sélectionné.</p>'
        active={key:row for key,row in providers.items() if row.get('enabled') or key=='ollama'}
        options=''.join('<option value="'+e(key)+'">'+e(key+' · '+row['type'])+'</option>' for key,row in active.items())
        routing=c.get('model_routing',{})
        for purpose,(label,role) in PURPOSES.items():
            saved=desk.settings('ai:route:'+purpose,routing.get(purpose,{})) or {}
            provider_id=saved.get('provider') or routing.get(role+'_provider') or 'ollama'
            model=saved.get('model') or routing.get(role+'_model') or c.get('ollama',{}).get('model','')
            selected_options=options.replace('value="'+e(provider_id)+'"','value="'+e(provider_id)+'" selected',1)
            out+=form('save_ai_route','Tester et utiliser',{'purpose':purpose},
                '<label>'+e(label)+' — fournisseur<select name="provider_id">'+selected_options+'</select></label>'
                '<label>Nom exact du modèle<input name="model" required maxlength="160" value="'+e(model)+'"></label>')
        out+='<p>Le contrôle indépendant peut utiliser un autre fournisseur et un autre modèle. Les contenus envoyés, les clés et les réponses complètes ne sont jamais inscrits dans le journal technique.</p>'
    elif selected=='extensions':
        from .ai_gateway import PURPOSES
        from .extensions364 import list_items
        items=list_items(desk)
        out+='<p>Référencez une fiche Lawve.ai, puis configurez son endpoint ou importez son archive. AxiorHub ne télécharge et n’exécute jamais automatiquement du code depuis le catalogue. Les connecteurs MCP sont testés sans donnée client ; les compétences et plugins restent en quarantaine jusqu’à votre revue.</p>'
        out+='<p><a rel="noopener noreferrer" target="_blank" href="https://lawve.ai/fr/connectors">Catalogue des connecteurs</a> · <a rel="noopener noreferrer" target="_blank" href="https://lawve.ai/fr/skills">Catalogue des compétences</a> · <a rel="noopener noreferrer" target="_blank" href="https://lawve.ai/fr/plugins">Catalogue des plugins</a></p>'
        purpose_checks='<fieldset><legend>Fonctions autorisées pour les instructions du skill/plugin</legend>'+''.join(
          '<label class="ws-check"><input type="checkbox" name="purposes" value="'+e(key)+'"> '+e(label)+'</label>'
          for key,(label,_) in PURPOSES.items())+'</fieldset>'
        out+=form('register_lawve_extension','Enregistrer sans activer',extra=
          '<label>URL de la fiche Lawve.ai<input type="url" name="source_url" required maxlength="1000" placeholder="https://lawve.ai/@auteur/connector/nom"></label>'
          '<label>Nom affiché<input name="name" maxlength="160"></label>'
          '<label>Endpoint MCP HTTPS — connecteurs uniquement<input type="url" name="endpoint" maxlength="1000" placeholder="https://exemple.fr/mcp/"></label>'
          '<label>Authentification<select name="auth_type"><option value="none">Aucune</option><option value="bearer">Jeton Bearer</option><option value="oauth2">OAuth 2.1 — jeton d’accès facultatif</option></select></label>'
          '<label>Jeton nouveau — jamais réaffiché<input type="password" name="token" maxlength="4096" autocomplete="new-password"></label>'
          '<label>Licence déclarée<input name="license" maxlength="80" placeholder="Apache 2.0, MIT, propriétaire…"></label>'+
          purpose_checks+
          '<label class="ws-check"><input type="checkbox" name="allow_external" value="yes"> J’autorise ce connecteur, s’il est ensuite activé, à recevoir les seules données nécessaires à sa fonction</label>')
        out+='<div class="ws-provider-grid">'
        for item in items:
            test=item.get('last_test') or {};review=item.get('security_review') or {}
            out+='<article class="ws-provider ws-extension"><h3>'+e(item.get('name'))+' · '+e(item.get('kind'))+'</h3>'
            out+='<p><strong>'+e(item.get('status','non testé'))+'</strong> · '+e(item.get('author',''))+' / '+e(item.get('slug',''))+'</p>'
            out+='<p><a rel="noopener noreferrer" target="_blank" href="'+e(item.get('source_url',''))+'">Voir la fiche Lawve.ai</a></p>'
            if item.get('endpoint'):out+='<p>Endpoint : '+e(item['endpoint'])+' · '+e(item.get('transport'))+' · '+e(item.get('auth_type'))+'</p>'
            if test:out+='<p class="'+('success' if test.get('status')=='ok' else 'notice')+'">'+e(test.get('message') or test.get('error'))+' '+(('· '+str(test.get('tools_count'))+' outil(s) déclaré(s)') if 'tools_count' in test else '')+'</p>'
            if item.get('archive_present'):
                out+='<p>Archive : SHA-256 '+e(item.get('archive_sha256',''))+' · '+str(len(item.get('archive_inventory',[])))+' fichier(s) déclaratif(s).</p>'
            if review:
                out+='<details><summary>Rapport de sécurité</summary><p>'+e(review.get('message',''))+'</p><p>Fichiers bloqués : '+e(', '.join(review.get('blocked_files',[])) or 'aucun')+'. Aucun script exécuté.</p></details>'
            if item.get('kind') in ('skill','plugin'):
                out+='<label>Archive ZIP téléchargée depuis la fiche<input type="file" accept=".zip,application/zip" data-extension-upload="'+e(item['id'])+'"></label><p role="status" data-extension-status="'+e(item['id'])+'">'+('Archive importée ; examiner le rapport avant activation.' if item.get('archive_present') else 'Aucune archive importée.')+'</p>'
            out+='<div class="actions">'+form('test_lawve_extension','Tester / contrôler',{'extension_id':item['id']})
            out+=form('set_lawve_extension','Désactiver' if item.get('enabled') else 'Activer après revue',
              {'extension_id':item['id'],'enabled':'no' if item.get('enabled') else 'yes'},
              '' if item.get('enabled') else '<label class="ws-check"><input type="checkbox" name="reviewed" value="yes" required> J’ai vérifié la fiche, la licence, les permissions et le rapport de sécurité</label>')
            out+='</div></article>'
        if not items:out+='<p class="empty">Aucune extension Lawve.ai enregistrée.</p>'
        out+='</div><p class="notice">Le registre enregistre, contrôle et active les extensions déclaratives. Il n’exécute ni scripts de plugin ni commandes stdio. Un connecteur activé n’est appelé que par un parcours métier explicitement autorisé ; son activation seule ne lui transmet aucun dossier.</p>'
    elif selected=='modeles':
        out+='<p>Importer un modèle Word privé, examiner les pages de l’original et d’un exemple, valider le modèle, créer des courriers et de nouvelles versions après édition Nextcloud / OnlyOffice.</p>'+link('/modeles-word','Ouvrir les documents du cabinet')+' · '+link('/audiences-word','Voir les autres outils Word')
    elif selected=='automatismes':
        groups=[('automatic_mail_drafts_enabled','Dépôt des réponses sûres dans Brouillons',c.get('orchestrator',{}).get('automatic_mail_drafts_enabled',True)),
                ('automatic_legal_projects_enabled','Préparation des projets d’actes',c.get('orchestrator',{}).get('automatic_legal_projects_enabled',True)),
                ('preparation34_enabled','Analyse 4 h, briefing 7 h 30 et audiences J−14',c.get('preparation34',{}).get('enabled',True)),
                ('sync_enabled','Découverte des dossiers',c.get('automation',{}).get('sync_enabled',True)),
                ('index_all_enabled','Indexation progressive',c.get('automation',{}).get('index_all_enabled',False))]
        out+='<p><strong>ON :</strong> les automatisations actives préparent les brouillons de courriel dans Brouillons et les projets de documents selon les contrôles existants. <strong>OFF :</strong> elles attendent votre demande manuelle depuis le courriel ou le dossier. L’envoi d’un courriel, la signature et le dépôt d’un acte restent soumis à une décision distincte.</p>'
        for key,label,default in groups:
            active=bool(desk.settings('automation:'+key,default))
            out+='<div class="ws-setting-row"><div><strong>'+e(label)+'</strong><small>'+('ON · automatique' if active else 'OFF · demande manuelle')+'</small></div>'+form('automation_setting','Passer sur OFF' if active else 'Passer sur ON',{'key':key,'value':'no' if active else 'yes','back':'settings'})+'</div>'
        out+='<p>La préparation proactive utilise le worker en place. Le briefing affiche les sources manquantes ou trop anciennes. '+link('/preparation-proactive','Consulter le briefing et les nouveautés')+'</p>'
    elif selected=='confidentialite':
        out+='<p>Mode courant : <strong>'+e(c.get('mode','non configuré'))+'</strong>. Dossiers autorisés : '+e(', '.join(c.get('nextcloud',{}).get('roots',[])))+'.</p><p>Les recherches affichées ici interrogent le stockage local du cabinet. Les données des clients ne sont pas envoyées au navigateur en dehors des résultats affichés dans votre session authentifiée.</p>'
    else:
        from .update420 import policy as update_policy
        updates=update_policy(desk)
        out+='<p>Consulter les exceptions, les traitements et la dernière synchronisation.</p>'+link('/administration','Diagnostics et historique')+' · '+link('/etat-systeme','État du système')+' · '+link('/qualite','Qualité et statistiques')+' · '+link('/associations','Associations à confirmer')
        out+='<h3>Mises à jour signées</h3><p>Le canal stable privilégie les versions validées. Le canal test permet une recette anticipée. Dans les deux cas, une archive distante est refusée sans signature Minisign valide.</p>'
        out+=form('save_update_policy420','Enregistrer la politique de mise à jour',extra=
          '<label>Canal<select name="channel"><option value="stable"'+(' selected' if updates['channel']=='stable' else '')+'>Stable</option><option value="test"'+(' selected' if updates['channel']=='test' else '')+'>Test</option></select></label>'
          '<label>URL HTTPS du manifeste de versions<input type="url" name="metadata_url" maxlength="1000" value="'+e(updates.get('metadata_url',''))+'" placeholder="https://updates.example.com/axiorhub/releases.json"></label>'
          '<p><strong>Signature obligatoire.</strong> Clé publique : <code>'+e(updates['minisign_public_key_file'])+'</code>.</p>')
        out+='<p>Commande : <code>sudo axiorhub update</code>. Une archive locale peut être contrôlée avec <code>sudo axiorhub update /chemin/version.tar.gz</code>. La sauvegarde et le retour arrière restent disponibles avant toute migration.</p>'
    return out+'</section>'
