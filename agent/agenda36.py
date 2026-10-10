"""Live CalDAV list, week and month views with only unambiguous case links."""
import calendar
from datetime import date, datetime, timedelta, timezone
from html import escape
from zoneinfo import ZoneInfo

from .common import Stop
from .improvements36 import matter_option
from .common import load_matters
from .workplan import calendar_events,_calendar_urls,EVENT_UID

JOURS = ('Lundi', 'Mardi', 'Mercredi', 'Jeudi', 'Vendredi', 'Samedi', 'Dimanche')   # 5.6.24 : libellés indépendants de la locale du serveur
MOIS = ('janvier', 'février', 'mars', 'avril', 'mai', 'juin', 'juillet', 'août', 'septembre', 'octobre', 'novembre', 'décembre')


def e(value):return escape(str(value if value is not None else ''),quote=True)


def page(desk,args,link,url,form=None):
    # 5.6.14 (U03) : semaine par défaut ; une vue choisie explicitement devient la préférence de l'utilisateur
    explicit=args.get('view','')
    if explicit in ('list','week','month'):
        view=explicit
        try:
            if desk.settings('agenda5614:view','week')!=view:desk.setting('agenda5614:view',view)
        except Exception:pass
    else:
        view=desk.settings('agenda5614:view','week')
        if view not in ('list','week','month'):view='week'
    try:day=date.fromisoformat(args.get('date','') or datetime.now(ZoneInfo('Europe/Paris')).date().isoformat())
    except ValueError:raise Stop('date_agenda_invalide') from None
    if view=='week':
        first=day-timedelta(days=day.weekday());last=first+timedelta(days=7)
    else:
        first=day.replace(day=1)
        last=(first.replace(year=first.year+1,month=1) if first.month==12
              else first.replace(month=first.month+1))
    tz=ZoneInfo(desk.c.get('calendar',{}).get('timezone','Europe/Paris'))
    begin=datetime.combine(first,datetime.min.time(),tz)
    end=datetime.combine(last,datetime.min.time(),tz)
    # Refresh on demand; the same view can be used during a CalDAV outage
    # with its last cached data and an explicit warning.
    warning='';configured=bool(_calendar_urls(desk))
    try:
        if not configured:raise Stop('aucun_calendrier_configure')
        data=calendar_events(desk,begin.isoformat(),end.isoformat(),refresh=True)
    except (Stop,OSError,TimeoutError,ValueError,KeyError):
        data=calendar_events(desk,begin.isoformat(),end.isoformat(),refresh=False)
        warning=('Lecture CalDAV indisponible : affichage du cache local, potentiellement incomplet.'
                 if configured else 'Aucun calendrier configuré : affichage du cache local uniquement.')
    events=data['events']
    by_day={}
    matters=[]
    try:matters=load_matters(desk.c)
    except Stop:matters=[]
    cfg=desk.c.get('nextcloud_workflow') or {}
    writable=bool(form and cfg.get('enabled') and cfg.get('planning_calendar_url'))

    def matter_select(selected=''):
        return '<label>Dossier<select name="matter"><option value="">Sans dossier</option>'+''.join(
            '<option value="'+e(m['id'])+'"'+(' selected' if m['id']==selected else '')+'>'+e(matter_option(m))+'</option>' for m in matters)+'</select></label>'

    from .agenda520 import enabled as personal_enabled
    personal=bool(form and cfg.get('enabled') and personal_enabled(desk))

    def personal_editor(event,start,ending):
        if not personal or event.get('recurrence_id'):
            return ''
        all_day=start.time()==datetime.min.time() and ending.time()==datetime.min.time() and (ending-start).days>=1
        minutes=max(5,int((ending-start).total_seconds()//60))
        when=('<label>Date<input type="date" name="start" required value="'+e(start.date().isoformat())+'"></label>' if all_day else
              '<label>Début<input type="datetime-local" name="start" required value="'+e(start.strftime('%Y-%m-%dT%H:%M'))+'"></label>'
              '<label>Durée (minutes)<input type="number" name="duration_minutes" min="5" max="1440" step="5" value="'+str(minutes)+'"></label>')
        return ('<details class="ws-task-editor"><summary>Modifier (agenda personnel)</summary>'+form('edit_personal_event','Enregistrer dans Nextcloud',{'event':event['id']},
            '<label>Intitulé<input name="title" required maxlength="300" value="'+e(event['title'])+'"></label>'+when)+
            '<small>Seuls l’intitulé et l’horaire sont réécrits ; rappels, participants et catégories sont conservés. Séries récurrentes non modifiables ici.</small></details>')

    def editor(event,start,ending):
        if not (writable and EVENT_UID.fullmatch(str(event.get('uid','')))):
            return personal_editor(event,start,ending)
        minutes=max(5,int((ending-start).total_seconds()//60))
        ident={'uid':event['uid']}
        return ('<details class="ws-task-editor"><summary>Modifier ou supprimer</summary>'+form('edit_agenda_event','Enregistrer les modifications',ident,
            '<label>Intitulé<input name="title" required minlength="3" maxlength="200" value="'+e(event['title'])+'"></label>'+matter_select(event['matter'])+
            '<label>Début<input type="datetime-local" name="start" required value="'+e(start.strftime('%Y-%m-%dT%H:%M'))+'"></label>'
            '<label>Durée (minutes)<input type="number" name="duration_minutes" min="5" max="1440" step="5" value="'+str(minutes)+'"></label>')+
            form('cancel_agenda_event','Supprimer cet événement',{**ident,'confirm':'yes'})+'</details>')

    def event_card(event):
        start=datetime.fromisoformat(event['starts']).astimezone(tz)
        ending=datetime.fromisoformat(event['ends']).astimezone(tz)
        label=e(event['title'] or 'Événement')
        when=e(start.strftime('%H:%M')+' – '+ending.strftime('%H:%M'))
        if event['matter']:
            return '<article class="ws-calendar-event"><time>'+when+'</time><strong>'+label+'</strong>'+link('/matter','Ouvrir le dossier',id=event['matter'])+editor(event,start,ending)+'</article>'
        details=' · Rapprochement ambigu' if len(event['matter_candidates'])>1 else ' · Aucun dossier relié'
        return '<article class="ws-calendar-event"><time>'+when+'</time><strong>'+label+'</strong><small>'+details+'</small>'+editor(event,start,ending)+'</article>'
    for event in events:
        begin_local=datetime.fromisoformat(event['starts']).astimezone(tz).date()
        end_local=(datetime.fromisoformat(event['ends']).astimezone(tz)-timedelta(microseconds=1)).date()
        cursor=max(first,begin_local)
        while cursor<=end_local and cursor<last:
            by_day.setdefault(cursor,[]).append(event)
            cursor+=timedelta(days=1)
    delta=timedelta(days=7 if view=='week' else 1)
    previous=(first-delta if view=='week' else first-timedelta(days=1))
    following=(last if view=='week' else last)
    out='<section class="ws-calendar"><h2>Événements de l’agenda</h2>'
    if writable:
        out+=('<details class="ws-task-editor"><summary>➕ Ajouter un événement à l’agenda</summary>'+form('create_agenda_event','Créer l’événement',None,
            '<label>Intitulé<input name="title" required minlength="3" maxlength="200" placeholder="Rendez-vous client, audience, appel…"></label>'+matter_select()+
            '<label>Début<input type="datetime-local" name="start" required value="'+day.isoformat()+'T09:00"></label>'
            '<label>Durée (minutes)<input type="number" name="duration_minutes" min="5" max="1440" step="5" value="60"></label>')+'</details>')
    if form is not None:
        from .agenda520 import enabled as personal_on
        from .reminders520 import settings as reminder_settings
        rs=reminder_settings(desk)
        out+=('<details class="ws-task-editor"><summary>Réglages : modification de mes agendas et rappels</summary>'+
            form('set_agenda_personal_edit','Enregistrer',None,'<label><input type="checkbox" name="enabled" value="yes"'+(' checked' if personal_on(desk) else '')+
                 '> Autoriser la modification de mes agendas et tâches personnels (titre, horaire, échéance, état ; jamais de suppression)</label>')+
            form('save_reminders520','Enregistrer les rappels',None,
                 '<label><input type="checkbox" name="push" value="yes"'+(' checked' if rs['push'] else '')+'> Rappels d’échéances et de prescriptions par notification mobile (sans contenu)</label>'
                 '<label><input type="checkbox" name="email" value="yes"'+(' checked' if rs['email'] else '')+'> Récapitulatif déposé dans votre boîte de réception (aucun envoi)</label>')+
            '</details>')
    if not writable and form is not None:
        out+='<p class="notice">La création et la modification d’événements demandent le compte Nextcloud technique et un calendrier de planification (<code>sudo python3 configure-nextcloud-workflow.py</code>). L’agenda reste lisible.</p>'
    out+='<nav class="ws-calendar-nav" aria-label="Vues de l’agenda">'+''.join(
        '<a '+('aria-current="page" ' if view==v else '')+'href="'+e(url('/planning',vue='agenda',view=v,date=day.isoformat()))+'">'+label+'</a>'
        for v,label in [('list','Liste'),('week','Semaine'),('month','Mois')])+'</nav>'
    out+='<div class="ws-calendar-nav">'+link('/planning','← Précédent',vue='agenda',view=view,date=previous.isoformat())+'<strong>'+e(first.strftime('%d/%m/%Y'))+' – '+e((last-timedelta(days=1)).strftime('%d/%m/%Y'))+'</strong>'+link('/planning','Suivant →',vue='agenda',view=view,date=following.isoformat())+'</div>'
    out+='<p>'+str(len(events))+' événement(s) sur cette période · '+str(data['counts']['linked'])+' relié(s) à un dossier certain.</p>'
    if warning:out+='<p class="notice">'+e(warning)+'</p>'
    if view=='month':
        out+='<div class="ws-calendar-month">'
        for week in calendar.Calendar(firstweekday=0).monthdatescalendar(first.year,first.month):
            for item in week:
                out+='<section class="ws-calendar-day'+(' ws-calendar-outside' if item.month!=first.month else '')+'"><h3>'+e(item.strftime('%a %d/%m'))+'</h3>'
                out+=''.join(event_card(ev) for ev in by_day.get(item,[]))
                out+='</section>'
        out+='</div>'
    else:
        if view=='week':
            days=[first+timedelta(days=x) for x in range(7)]
            if writable:
                tasks=[dict(r) for r in desk.db.execute("SELECT id,title FROM work_tasks_v211 WHERE status IN ('todo','in_progress') ORDER BY due LIMIT 30")]
                tasks+=[dict(r) for r in desk.db.execute("SELECT id,title FROM tasks WHERE status='open' AND id NOT IN (SELECT id FROM work_tasks_v211) ORDER BY due LIMIT 20")]
                if tasks:
                    out+=('<section class="ax-dnd" aria-label="Tâches à programmer"><p><strong>Glissez une tâche sur un jour</strong> pour la programmer (heure et durée à confirmer).</p><div class="ax-dnd-tasks">'+
                          ''.join('<span class="ax-dnd-task" draggable="true" tabindex="0" data-task="'+e(x['id'])+'">'+e(x['title'][:90])+'</span>' for x in tasks)+'</div>'+
                          '<div class="ax-dnd-form" hidden>'+form('schedule_work_task','Programmer dans l’agenda',{'task':''},
                              '<p class="ax-dnd-label"></p><label>Début<input type="datetime-local" name="start" required></label>'
                              '<label>Durée (minutes)<input type="number" name="duration_minutes" value="60" min="15" max="480" step="15"></label>')+'</div></section>')
            out+='<div class="ws-calendar-week">'
        else:
            days=sorted(by_day)
            out+='<div class="ws-calendar-list">'
        for item in days:
            out+='<section class="ws-calendar-day" data-day="'+e(item.isoformat())+'"><h3>'+e(JOURS[item.weekday()]+item.strftime(' %d/%m/%Y'))+'</h3>'   # 5.6.24 : jours en français
            out+=''.join(event_card(ev) for ev in by_day.get(item,[]))
            if not by_day.get(item):out+='<small>Aucun événement connu</small>'
            out+='</section>'
        if not days:out+='<p>Aucun événement connu pour cette période.</p>'
        out+='</div>'
    return out+'</section>'
