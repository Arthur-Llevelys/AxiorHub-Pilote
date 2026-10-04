"""Server-rendered folder management and private, scoped conversation views."""
import json
from urllib.parse import urlencode, urlsplit
from .common import Stop,fold
from .improvements36 import matter_option, matter_label
from .workspace import allowed,chat_scope,history


def directory_panel(c,desk,matters,args,form,link):
    from .web import e,block
    roots=c['nextcloud'].get('matter_roots') or c['nextcloud']['roots']
    out='<section class="card"><h2>Retrouver un dossier dans Nextcloud</h2><p>La recherche ci-dessus porte aussi sur les répertoires déjà trouvés, même sans référence numérique. Si un chemin manque, recherchez directement son dossier parent.</p>'
    if args.get('mail_key'):
        out+='<p class="notice">Après avoir créé ou enregistré l’affaire, '+link('/mail','revenir au courriel et sélectionner son dossier',key=args['mail_key'])+'. Les associations de courriels ne sont jamais déduites de la création d’un répertoire.</p>'
    out+=form('browse','Lire ce répertoire dans Nextcloud',extra='<label>Chemin du répertoire<input required name="path" value="'+e(roots[0])+'" placeholder="/CABINET EXEMPLE/01 - Dossiers"></label>')
    paths={m['path'] for m in matters};q=fold(args.get('q',''))
    page=max(0,min(500,int(args.get('page','0'))))
    candidates=[]
    for row in desk.db.execute('SELECT path FROM directories ORDER BY path'):
        p=row[0]
        try:allowed(c,p)
        except Stop:continue
        if p not in paths and p not in roots and q in fold(p):candidates.append(p)
    out+='<h3>Répertoires trouvés, à enregistrer si ce sont des affaires</h3>'
    for p in candidates[page*50:page*50+50]:
        out+='<p>'+e(p)+'<br>'+link('/dossiers','Enregistrer comme dossier',path=p,q=args.get('q',''))+'</p>'
    if not candidates:out+='<p>Aucun répertoire non enregistré correspondant dans le catalogue actuel.</p>'
    if page:out+=link('/dossiers','Page précédente',q=args.get('q',''),page=page-1)+' · '
    if len(candidates)>(page+1)*50:out+=link('/dossiers','Page suivante',q=args.get('q',''),page=page+1)
    selected=args.get('path','')
    out+='<h2>Enregistrer une affaire existante</h2><p>Indiquez le chemin exact, sa référence et le client. Le répertoire est vérifié dans Nextcloud, puis son indexation démarre. Les correspondants se renseignent ensuite dans sa fiche.</p>'
    out+=form('register_matter','Vérifier et enregistrer le dossier',extra=
        '<label>Chemin Nextcloud exact<input required name="path" value="'+e(selected)+'" placeholder="/CABINET EXEMPLE/01 - Dossiers/CLIENT - Affaire - 2026090901"></label>'
        '<label>Référence unique<input required name="reference" pattern="[A-Za-z0-9_-]{1,80}" maxlength="80" placeholder="2026090901"></label>'
        '<label>Nom du client<input required name="client_name" maxlength="200"></label>'
        '<label>Références dans les objets des courriels<input name="references" placeholder="CLIENT, NOM ADVERSE"></label>')
    out+='<h3>Créer une nouvelle affaire</h3><p>Créez d’abord le répertoire dans Nextcloud, puis enregistrez son chemin avec ce formulaire. Le nom peut être « CLIENT - Affaire - 2026090901 ». Le bouton ci-dessus ajoute l’affaire à l’agent ; il ne crée pas de répertoire sur le serveur.</p>'
    out+='<details><summary>Créer aussi le répertoire dans Nextcloud</summary><p>Cette action crée un dossier vide sous une racine autorisée, puis l’enregistre. Elle ne déplace et ne supprime aucun fichier. Vérifiez soigneusement le chemin complet.</p>'+form('create_matter','Créer le répertoire et enregistrer l’affaire',{'confirm':'yes'},
        '<label>Nouveau chemin Nextcloud complet<input required name="path" placeholder="/CABINET EXEMPLE/01 - Dossiers/CLIENT - Affaire - 2026090901"></label>'
        '<label>Référence unique<input required name="reference" pattern="[A-Za-z0-9_-]{1,80}" maxlength="80"></label>'
        '<label>Nom du client<input required name="client_name" maxlength="200"></label>'
        '<label>Références dans les objets<input name="references"></label>')+'</details>'
    nc=c['nextcloud'].get('url','')
    if urlsplit(nc).scheme=='https':
        out+='<p><a rel="noreferrer" target="_blank" href="'+e(nc.rstrip('/')+'/index.php/apps/files/?'+urlencode({'dir':roots[0]}))+'">Ouvrir les fichiers Nextcloud</a></p>'
    return out+'</section>'


def assistant_panel(c,desk,matters,args,form,link):
    from .web import e,block,date
    from .integration import thread_messages,threads
    out='<p class="success">💬 Vous pouvez interroger tout le cabinet sans choisir de dossier. La conversation ne dépose aucun brouillon sans une action explicite ; pour en créer un, ouvrez plutôt la conversation depuis le courriel concerné.</p>'
    selected=args.get('thread','');thread=None
    if selected:
        thread=desk.db.execute('SELECT * FROM assistant_threads WHERE id=?',(selected,)).fetchone()
        if not thread:raise Stop('conversation_absente')
    matter_id=args.get('matter','') or (thread['matter'] if thread else '')
    mail_key=args.get('key','') or (thread['mail_key'] if thread else '')
    matter=next((m for m in matters if m['id']==matter_id),None) if matter_id else None
    report=None
    if mail_key:
        from .desk import report_for
        report=report_for(c,mail_key)
    if matter:out+='<h2>📁 '+e(matter_option(matter))+'</h2><p>'+e(matter['path'])+' · '+link('/matter','Ouvrir la fiche',id=matter['id'])+'</p>'
    elif not report:out+='<h2>🔎 Recherche générale dans le cabinet</h2><p>Exemples : « Dans quels dossiers ai-je rédigé des CGV e-commerce ? » ou « Où ai-je traité un transfert de siège de SARL ? »</p>'
    if report:out+='<h3>✉️ '+e(report.get('subject','Courriel'))+'</h3><p>'+link('/mail','Ouvrir le courriel',key=mail_key)+'</p>'
    fields={'matter':matter_id,'key':mail_key,'thread':selected,'attachment_id':''}
    out+=form('assistant_ask','Envoyer à l’assistant',fields,
        '<label>Votre question ou consigne<textarea required name="question" rows="5" maxlength="12000" placeholder="Posez une question générale, analysez un dossier ou demandez un projet de réponse…"></textarea></label>')
    out+='<div class="assistant-upload"><label>Joindre un document à la prochaine demande (.pdf, .docx, .txt, .md, .csv, .odt, .eml ; 8 Mo max.)<input type="file" data-assistant-file accept=".pdf,.docx,.txt,.md,.csv,.odt,.eml"></label><p data-assistant-status role="status">Texte extrait et traité par sections, jusqu’à 500 000 caractères. Chaque section est analysée avant la synthèse. Les images et tableaux exigent une relecture du document original.</p></div>'
    if thread:
        messages=thread_messages(desk,selected)
        out+='<section class="conversation">'
        for row in messages:
            who='Vous' if row['role']=='user' else 'Assistant — à relire'
            icon='👤' if row['role']=='user' else '🤖'
            out+='<article class="message '+e(row['role'])+'"><small>'+e(date(row['created']))+'</small><h3>'+icon+' '+who+'</h3>'+block(row['content'])
            if row['status'] in ('queued','running'):
                out+='<p class="notice">⏳ '+('En file d’attente prioritaire' if row['status']=='queued' else 'Réponse en cours de génération')+'.</p>'
            elif row['status']=='error':out+='<p class="notice">⚠️ La réponse n’a pas pu être produite. Consultez l’opération correspondante.</p>'
            try:sources=json.loads(row['sources'] or '[]')
            except ValueError:sources=[]
            if sources:
                out+='<details><summary>📚 Sources citées ('+str(len(sources))+')</summary>'
                for source in sources:
                    out+='<h4>'+e(source.get('path') or source['id'])+'</h4><small>'+e(source.get('matter',''))+' · '+e(source.get('modified',''))+'</small>'+block(source.get('excerpt',''))
                out+='</details>'
            out+='</article>'
        out+='</section>'
        if mail_key:
            last_instruction=next((row['content'] for row in reversed(messages) if row['role']=='user'), '')
            out+=form('prepare_reply','✍️ Créer un brouillon pour ce courriel',{'key':mail_key,'instruction':last_instruction[:2000]})
    if not thread and (matter_id or mail_key):
        legacy_scope,_,_=chat_scope(c,{'matter':matter_id,'key':mail_key})
        legacy=history(desk,legacy_scope)
        if legacy:out+='<h2>Historique antérieur à la 1.6.0</h2>'
        for row in reversed(legacy):
            result=json.loads(row['response']);sources=json.loads(row['sources'])
            out+='<article class="card"><small>'+e(date(row['created']))+'</small><h3>Vous</h3>'+block(row['question'])+'<h3>Assistant — à relire</h3>'+block(result['answer'])
            out+='<details><summary>Sources fournies ('+str(len(sources))+')</summary>'
            for source in sources:
                out+='<h4>'+e(source.get('path') or source.get('subject') or source['id'])+'</h4>'+block(source.get('excerpt',''))
            out+='</details></article>'
    recent=threads(desk,30)
    out+='<h2>🕘 Historique des conversations</h2><div class="thread-list">'
    for item in recent:
        scope='Cabinet' if not item['matter'] and not item['mail_key'] else ('Dossier '+item['matter'] if item['matter'] else 'Courriel')
        out+='<p>'+link('/assistant',item['title'],thread=item['id'])+'<small>'+e(scope+' · '+date(item['updated']))+'</small></p>'
    if not recent:out+='<p class="empty">Aucune conversation pour le moment.</p>'
    out+='</div><p class="muted">Les réponses s’appuient sur les contenus déjà indexés et indiquent leurs sources. Les conversations sont conservées 90 jours et ne déclenchent aucun envoi automatique.</p>'
    return out
