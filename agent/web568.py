"""Écrans et API 5.6.8, authentifiés par la même enveloppe WSGI et le même CSRF."""
from html import escape as e
import json

from .common import Stop,load_matters,matter_display
from . import settings568,proactive568,plans568,voice568,news568
from .web567 import actor

MESSAGES={
 'adresse_utilisateur_requise':'Indiquez l’adresse de l’utilisateur dont les envois doivent être suivis.',
 'identite_hors_compte_configure':'Choisissez une identité déjà autorisée dans Connexions › Courriels.',
 'identite_deja_observee':'Cette identité est déjà suivie par un autre profil actif ; cela évite les doublons.',
 'condition_a_confirmer':'Confirmez que la condition mentionnée dans le message a été réalisée.',
 'source_veille_officielle_requise':'Utilisez une URL HTTPS d’un flux officiel autorisé (Justice, Cour de cassation, Conseil d’État, CNIL ou institutions européennes).',
 'creation_talk_incertitude_reconcilier_avant_relance':'La création du salon est incertaine. AxiorHub relira les salons existants ; il ne recréera pas un salon sans preuve.',
 'budget_voix_externe_non_configure':'Renseignez le tarif, le plafond par lecture et le budget mensuel vocal avant un appel ElevenLabs.',
 'budget_voix_requete_depasse':'Cette lecture dépasse le plafond vocal par requête.',
 'budget_voix_mensuel_depasse':'Le budget vocal du mois est atteint. La lecture locale reste disponible.',
 'preuve_execution_requise':'Indiquez une preuve ou confirmation d’exécution. Un projet préparé ne signifie pas que la promesse a été exécutée.',
 'role_insuffisant':'Votre rôle ne permet pas cette action.',
}


def human(code):
    from .web440 import human as old
    from .web_rules568 import MESSAGES as docs
    return MESSAGES.get(code,docs.get(code,old(code)))


def api(env,desk,auth,prefix,name,args,method):
    from .web440 import json_out,check_post
    owner,role=actor(env);admin=role=='administrateur';route=name[5:]
    if route.startswith(('rules/','document/','calendars/','google/','procedure/')):
        from .web_rules568 import api as rules_api
        return rules_api(env,desk,auth,prefix,route,args,method)
    if method=='GET':
        if route=='profile':return json_out(settings568.profile(desk,owner))
        if route=='pulse':return json_out(proactive568.pulse(desk,owner))
        if route=='state':return json_out(proactive568.snapshot(desk,owner,False,prefix=prefix)) # vues privées, même pour admin
        if route=='news':return json_out(news568.listing(desk,owner))
        if route=='plan':return json_out(plans568.get(desk,str(args.get('id') or ''),owner,prefix=prefix))
        if route=='readiness':
            from .readiness569 import last
            return json_out(last(desk) or {'checks':[],'ok':None})
        if route=='audience':   # 5.6.13 : fiche de préparation par dossier
            from .audience5613 import sheet
            return json_out(sheet(desk,str(args.get('matter') or '')))
        if route=='mission5614':
            from . import taches5614
            return json_out(taches5614.get(desk,str(args.get('id') or ''),owner,admin,prefix))
        if route=='mission5614/list':
            from . import taches5614
            return json_out({'missions':taches5614.listing(desk,owner,admin,prefix)})
        if route=='profils':
            from . import profils5614
            return json_out({'profiles':profils5614.listing(desk)})
        if route=='ninja/missing':
            from . import facturation5614
            return json_out(facturation5614.missing_for(desk,str(args.get('matter') or ''),str(args.get('what') or 'quote')))
        if route=='capabilities':
            return json_out({'tts':desk.c.get('speech568',{}).get('provider','espeak'),'dictation':bool(desk.c.get('audio',{}).get('enabled')),
                    'continuous_turns':True,'progressive_speech':True,'full_duplex_streaming':False,'talk':settings568.profile(desk,owner)['talk_enabled'],
                    'roles':[{'id':k,'name':v[0],'description':v[1]} for k,v in settings568.ROLES.items()]})
        raise Stop('route_inconnue')
    if method!='POST':raise Stop('methode_refusee')
    data=check_post(env,auth)
    if route=='speech':return {'status':'200 OK','kind':'audio/wav','body':voice568.speech(desk,data.get('text',''),owner,str(data.get('matter') or ''))}
    if route=='speech/preview':return json_out(voice568.preview(desk,str(data.get('text') or '')[:7000],owner,str(data.get('matter') or '')))
    if route=='voice/turn':
        from .missions567 import create
        mode=data.get('mode','conversation')
        if mode not in ('conversation','mission'):raise Stop('mode_voix_invalide')
        if role not in ('administrateur','avocat','assistant'):raise Stop('role_insuffisant')
        if role=='assistant' and mode=='mission' and (data.get('context') or {}).get('mail_key'):raise Stop('role_insuffisant')
        return json_out(create(desk,{**data,'analysis_only':mode=='conversation','channel':'voice'},owner))
    if route=='plan/create':
        if role not in ('administrateur','avocat'):raise Stop('role_insuffisant')
        return json_out(plans568.create(desk,data,owner))
    if route=='plan/control':
        if role not in ('administrateur','avocat'):raise Stop('role_insuffisant')
        return json_out(plans568.control(desk,data,owner))
    if route in ('profile','event/control','commitment/control','scan','news/collect','talk/prepare'):
        if role not in ('administrateur','avocat'):raise Stop('role_insuffisant')
        if route=='profile':return json_out(settings568.save(desk,data,owner))
        if route=='event/control':return json_out(proactive568.control(desk,data,owner))
        if route=='commitment/control':return json_out(proactive568.commitment_control(desk,data,owner))
        if route=='scan':
            if not settings568.profile(desk,owner)['enabled']:raise Stop('proactivite_desactivee')
            job=desk.enqueue('sent568_scan',{'owner':owner},priority=10)
            return json_out({'job':job,'message':'Lecture des envois mise en file. Les résultats apparaîtront ici.'})
        if route=='news/collect':return json_out({'job':desk.enqueue('news568_collect',{'owner':owner},priority=15)})
        eid=str(data.get('id') or '')
        if not desk.db.execute('SELECT 1 FROM events_v568 WHERE id=? AND owner=?',(eid,owner)).fetchone():raise Stop('evenement_absent')
        return json_out({'job':desk.enqueue('talk568_prepare',{'event':eid,'owner':owner},priority=15)})
    if route=='readiness/run':
        if not admin:raise Stop('role_insuffisant')
        from .readiness569 import run
        return json_out(run(desk))
    if route=='readiness/production':   # 5.6.13 : recette de production réelle (données fictives, services réels)
        if not admin:raise Stop('role_insuffisant')
        from .recette5613 import run as recette
        return json_out(recette(desk))
    if route.startswith('mission5614/'):   # 5.6.14 : missions complexes
        if role not in ('administrateur','avocat'):raise Stop('role_insuffisant')
        from . import taches5614
        sub=route[len('mission5614/'):]
        if sub=='create':
            answers={k:data[k] for k in ('juridiction','voie','representation','montant_cents') if data.get(k)}
            payload={**data,'answers':{**(data.get('answers') or {}),**answers},'request_key':str(data.get('request_key') or '').strip() or __import__('secrets').token_hex(12)}
            if not payload.get('parcours'):
                from .parcours5614 import detect
                payload['parcours']=detect(str(data.get('instruction') or '')) or 'assignation'
            return json_out(taches5614.create(desk,payload,owner))
        if sub=='control':return json_out(taches5614.control(desk,data,owner,admin))
        if sub=='decide':return json_out(taches5614.decide(desk,data,owner,admin))
        if sub=='run':
            m=taches5614.get(desk,str(data.get('id') or ''),owner,admin)
            taches5614.advance(desk,m['id'])
            return json_out({'executed':taches5614.run_pending(desk,m['id']),'mission':taches5614.get(desk,m['id'],owner,admin,prefix)})
        if sub=='migrate':return json_out(taches5614.migrate_plan(desk,str(data.get('plan') or ''),owner))
        raise Stop('route_inconnue')
    if route=='decision/defer':
        from .aujourdhui5614 import defer
        kind=str(data.get('kind') or '')
        if kind=='mission5614':
            from . import taches5614
            from datetime import datetime,timedelta,timezone
            until=(datetime.now(timezone.utc)+timedelta(days=max(1,min(int(data.get('days') or 1),30)))).replace(hour=7,minute=0,second=0,microsecond=0).isoformat()
            return json_out(taches5614.decide(desk,{'id':str(data.get('id') or ''),'action':'defer','until':until},owner,admin) and {'deferred':True,'until':until})
        return json_out(defer(desk,str(data.get('key') or ''),int(data.get('days') or 1)))
    if route=='automatismes/pause':
        if role not in ('administrateur','avocat'):raise Stop('role_insuffisant')
        paused=bool(data.get('paused'))
        desk.setting('missions567:pause',paused);desk.audit('automatismes5614_pause',{'paused':paused})
        return json_out({'paused':paused,'message':'Automatismes en pause : aucune nouvelle mission ni tâche ne démarre.' if paused else 'Automatismes repris.'})
    if route.startswith('profils/'):
        if not admin and role!='avocat':raise Stop('role_insuffisant')
        from . import profils5614
        if route=='profils/approve':return json_out(profils5614.approve(desk,str(data.get('id') or ''),str(data.get('ack') or '')))
        if route=='profils/revoke':return json_out(profils5614.revoke(desk,str(data.get('id') or '')))
        raise Stop('route_inconnue')
    if route.startswith('ninja/'):
        if role not in ('administrateur','avocat'):raise Stop('role_insuffisant')
        from . import facturation5614
        if route=='ninja/client':return json_out(facturation5614.ensure_client(desk,str(data.get('matter') or ''),data,confirm=bool(data.get('confirm'))))
        if route=='ninja/clients/search':return json_out({'clients':facturation5614.search_clients(desk,str(data.get('name') or ''),str(data.get('email') or ''),str(data.get('siren') or ''))})
        if route=='ninja/project':return json_out(facturation5614.ensure_project(desk,str(data.get('matter') or ''),data))
        if route=='ninja/quote/preview':return json_out(facturation5614.preview_quote(desk,str(data.get('matter') or ''),data.get('items') or [],str(data.get('terms') or ''),data.get('validity_days') or 30,str(data.get('note') or '')))
        if route=='ninja/quote':return json_out(facturation5614.draft_quote(desk,str(data.get('matter') or ''),data.get('items') or [],confirm=data.get('confirm'),terms=str(data.get('terms') or ''),validity_days=data.get('validity_days') or 30,note=str(data.get('note') or '')))
        if route=='ninja/sync':return json_out({'changes':facturation5614.pull(desk),'message':'Statuts et paiements relus depuis Invoice Ninja.'})
        raise Stop('route_inconnue')
    if route=='docreq/control':         # 5.6.13 : « À décider » — demande de document bloquée reprise avec le dossier choisi
        if role not in ('administrateur','avocat'):raise Stop('role_insuffisant')
        from .docrequest520 import resolve
        return json_out(resolve(desk,str(data.get('id') or ''),str(data.get('matter') or '')))
    if route in ('test/talk','test/voice'):
        if not admin:raise Stop('role_insuffisant')
        if route=='test/talk':
            from .talk568 import diagnostic
            return json_out(diagnostic(desk))
        return json_out(voice568.diagnostic(desk))
    raise Stop('route_inconnue')


def page(desk,auth,prefix,env,path):
    from .web440 import shell
    if path=='/parametres/proactivite':return preferences(desk,auth,prefix,env)
    if path=='/mise-en-service':
        from .readiness569 import page as readiness_page   # 5.6.9
        return readiness_page(desk,auth,prefix,env)
    if path=='/audience':
        from .audience5613 import page as audience_page    # 5.6.13
        return audience_page(desk,auth,prefix,env,env.get('axiorhub.args',{}))
    if path=='/missions-complexes':
        from .missions5614_ui import page as missions_page   # 5.6.14
        return missions_page(desk,auth,prefix,env,env.get('axiorhub.args',{}))
    if path=='/profils':
        from .missions5614_ui import profils_page   # 5.6.14
        return profils_page(desk,auth,prefix,env)
    if path=='/veille':
        body='<h1>Veille juridique</h1><p>Flux officiels datés correspondant aux domaines configurés. Chaque extrait conserve sa source ; ce classement ne remplace pas la lecture ni l’analyse de la décision.</p><button type="button" data-run568="news/collect">Actualiser la veille</button><a href="'+e(prefix)+'/parametres/proactivite">Domaines et sources</a><p id="proactive568-status" role="status"></p><div id="news568-list"></div>'
        return shell('Veille',body,prefix,auth['csrf'],'/production')
    body='<header class="proactive568-heading"><h1>Engagements et suites de missions</h1><p>AxiorHub retrouve les promesses dans les courriels effectivement envoyés, prépare les suites autorisées et garde les preuves de chaque étape.</p><button type="button" data-run568="scan">Vérifier les envois</button><a href="'+e(prefix)+'/parametres/proactivite">Régler les initiatives</a></header>'
    body+='<p id="proactive568-status" role="status"></p><div id="proactive568-summary"></div><section><h2>Les suites préparées pour vous</h2><div id="plans568-list" class="mission567-grid"></div></section><details open><summary>Engagements à suivre</summary><div id="commitments568-list"></div></details><details><summary>Événements, décisions et abstentions</summary><div id="events568-list"></div></details>'
    return shell('Engagements',body,prefix,auth['csrf'],'/aujourdhui')


def preferences(desk,auth,prefix,env,shell=None):
    from .web440 import shell as default_shell   # 5.6.21
    shell=shell or default_shell
    owner,role=actor(env)
    if role not in ('administrateur','avocat'):raise Stop('role_insuffisant')
    p=settings568.profile(desk,owner)
    def field(key,label,typ='text',advanced=False):
        value=p[key]
        if typ=='checkbox':return '<label class="proactive568-check"><input name="'+key+'" type="checkbox"'+(' checked' if value else '')+'>'+e(label)+'</label>'
        if typ=='lines':return '<label>'+e(label)+'<textarea name="'+key+'" rows="3">'+e('\n'.join(value))+'</textarea></label>'
        return '<label>'+e(label)+'<input name="'+key+'" type="'+typ+'" value="'+e(str(value))+'"></label>'
    body='<h1>Initiatives et agents spécialisés</h1><p>Les rôles partagent les mêmes missions, modèles et permissions. Aucun modèle ne reste actif pour chaque rôle. Les envois, invitations, signatures et factures définitives demandent toujours une action explicite.</p><form id="proactive568-settings">'
    body+=field('enabled','Activer le suivi des envois et des suites de missions','checkbox')
    body+='<div class="mission567-fields">'+field('primary_address','Mon adresse d’envoi','email')+field('aliases','Alias autorisés (déjà configurés dans Connexions)','lines')+field('timezone','Fuseau horaire')+'</div>'
    body+='<label>Autonomie<select name="autonomy">'+''.join('<option value="'+v+'"'+(' selected' if p['autonomy']==v else '')+'>'+label+'</option>' for v,label in [('observe','Observer et proposer'),('prepare','Préparer automatiquement'),('organize','Préparer et organiser : seuls les effets déjà autorisés')])+'</select></label>'
    body+='<details><summary>Rôles et exclusions</summary>'
    for key,(name,desc) in settings568.ROLES.items():body+='<label class="proactive568-check"><input type="checkbox" name="roles" value="'+key+'"'+(' checked' if key in p['roles'] else '')+'>'+e(name)+'<small>'+e(desc)+'</small></label>'
    body+='<label>Dossiers exclus<select multiple name="excluded_matters" size="6">'+''.join('<option value="'+e(str(m['id']))+'"'+(' selected' if m['id'] in p['excluded_matters'] else '')+'>'+e(matter_display(m))+'</option>' for m in load_matters(desk.c))+'</select></label><label>Exceptions d’autonomie par dossier (une ligne : référence = observe/prepare/organize)<textarea name="matter_modes" rows="3">'+e('\n'.join(k+' = '+v for k,v in p['matter_modes'].items()))+'</textarea></label></details>'
    body+='<details><summary>Rendez-vous Nextcloud Talk</summary>'+field('talk_enabled','Préparer un salon privé pour les rendez-vous explicitement en visio','checkbox')+field('meeting_days','Anticipation en jours','number')+'<p>Lecture et création par votre compte Nextcloud. Aucun invité, partage ni modification d’agenda. Le salon doit être relu avec un seul participant avant le statut vérifié.</p><button type="button" data-run568="test/talk">Tester la lecture Talk</button></details>'
    body+='<details><summary>Veille du matin</summary>'+field('news_enabled','Activer la veille publique','checkbox')+field('legal_fields','Domaines ou mots-clés (un par ligne)','lines')+field('news_sources','URLs des flux RSS/Atom officiels (une par ligne)','lines')+field('briefing_time','Heure locale du contrôle de veille','time')+'<p>Rapprochement par mots-clés ; sources indisponibles signalées. Aucun dossier n’est transmis à un moteur de recherche.</p><a href="'+e(prefix)+'/veille">Lire la veille sourcée</a></details>'
    body+='<details><summary>Rythme, silence et conservation</summary><div class="mission567-fields">'+''.join(field(k,label,typ) for k,label,typ in [('lookback_days','Fenêtre initiale des envois (jours)','number'),('followup_days','Délai de suivi proposé (jours)','number'),('daily_plan_limit','Maximum quotidien de nouvelles suites','number'),('max_suggestions','Propositions visibles par défaut','number'),('quiet_start','Début de tranquillité','time'),('quiet_end','Fin de tranquillité','time'),('vacation_until','Mode vacances jusqu’au','date'),('retention_days','Conservation des nouveautés de veille (jours)','number')])+'</div><label>Jours de veille<select multiple name="weekdays">'+''.join('<option value="'+str(i)+'"'+(' selected' if i in p['weekdays'] else '')+'>'+v+'</option>' for i,v in enumerate(['Lundi','Mardi','Mercredi','Jeudi','Vendredi','Samedi','Dimanche']))+'</select></label></details><button>Enregistrer les réglages</button><p id="proactive568-status" role="status"></p></form>'
    body+='<section><h2>Voix et conversation rapide</h2><p>Le bouton micro à droite lance une conversation par tours continus : transcription locale, réponse du moteur configuré, lecture vocale et interruption de la lecture. Choisissez le mode discussion ou la préparation des missions internes.</p><a href="'+e(prefix)+'/parametres/connexions">Configurer Whisper, Kokoro, Chatterbox ou ElevenLabs dans Connexions</a><button type="button" data-run568="test/voice">Tester le fournisseur vocal</button><a href="'+e(prefix)+'/parametres/assistant">Nom, ton, vitesse et lexique</a></section>'
    body+='<section><h2>Mes agents documentaires</h2><p>Créez vos missions en langage naturel et sélectionnez les agendas destinataires.</p><a href="'+e(prefix)+'/parametres/agents">Créer ou modifier un agent</a> · <a href="'+e(prefix)+'/parametres/agendas">Agendas et profil procédural</a> · <a href="'+e(prefix)+'/agents-documents">Résultats et reprises</a></section>'
    return shell('Initiatives et voix',body,prefix,auth['csrf'],'/parametres')
