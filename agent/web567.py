"""Couche UI 5.6.7, intégrée au gabarit et à la sécurité existants."""
from html import escape as e
import json
import shutil

from .common import Stop
from . import assistant567, missions567


def actor(env):
    return str(env.get('HTTP_X_AXIORHUB_USER') or 'cabinet'), str(env.get('HTTP_X_AXIORHUB_ROLE') or 'administrateur')


def api(env, desk, auth, prefix, name, args, method):
    from .web440 import check_post, json_out
    owner, role = actor(env)
    if method == 'GET':
        if name == 'm567/missions':
            return json_out({'missions': missions567.listing(desk, owner, role == 'administrateur', prefix)})
        if name == 'm567/mission':
            try:
                return json_out(missions567.get(desk, args.get('id', ''), owner, role == 'administrateur', prefix))
            except Stop as ex:
                if str(ex) != 'mission_absente':
                    raise
                from . import taches5614   # 5.6.14 : mission complexe affichée dans le même panneau
                return json_out(missions567.complex_shim(taches5614.get(desk, args.get('id', ''), owner, role == 'administrateur', prefix)))
        if name == 'm567/profile':
            return json_out(assistant567.profile(desk, owner))
        if name == 'm567/briefing':
            from .voice568 import briefing
            return json_out(briefing(desk, owner))
        if name == 'm567/matters':      # 5.6.13 : sélection du dossier (client, adversaire, référence, alias ; récents en premier)
            from .pilote5613 import matters_listing
            return json_out({'matters': matters_listing(desk)})
        if name == 'm567/attachments':   # 5.6.13 : liste commune texte–voix des pièces jointes
            from .pilote5613 import attachments_listing
            idents = [x for x in str(args.get('ids', '')).split(',') if x]
            return json_out({'attachments': attachments_listing(desk, idents, str(args.get('matter') or ''), str(args.get('key') or ''))})
        if name == 'm567/capabilities':
            from .reception567 import capabilities
            return json_out({'missions': True, 'speech_local': bool(shutil.which('espeak-ng')),
                             'dictation_configured': bool(desk.c.get('audio', {}).get('enabled')),
                             **capabilities(desk)})
        if name.startswith('m567/config/'):
            if role != 'administrateur':raise Stop('role_insuffisant')
            from .config567 import handle
            return json_out(handle(desk,name,dict(args or {}),env,method))   # 5.6.24 : paramètres de lecture (navigation du dossier local)
        raise Stop('route_inconnue')
    if method != 'POST':
        raise Stop('methode_refusee')
    data = check_post(env, auth)
    if name == 'm567/mission/create':
        if role == 'assistant' and str((data.get('context') or {}).get('mail_key') or ''):
            raise Stop('depot_brouillon_role_insuffisant')
        return json_out(missions567.create(desk, data, owner))
    if name == 'm567/mission/intent':   # 5.6.13 : résultat attendu annoncé avant de démarrer
        from .pilote5613 import preview
        return json_out(preview(desk, data, owner))
    if name == 'm567/mission/control':
        if str(data.get('action') or '') == 'validate' and role not in ('administrateur', 'avocat'):
            raise Stop('role_insuffisant')
        return json_out(missions567.control(desk, data, owner, role == 'administrateur'))
    if name == 'm567/rule/decide':      # 5.6.13 : règle proposée après une correction
        if role not in ('administrateur', 'avocat'):
            raise Stop('role_insuffisant')
        from .pilote5613 import decide_rule
        return json_out(decide_rule(desk, str(data.get('id') or ''), str(data.get('action') or '')))
    if name == 'm567/profile':
        return json_out(assistant567.save_profile(desk, data, owner))
    if name == 'm567/speech':
        from .voice568 import speech
        return {'status': '200 OK', 'kind': 'audio/wav', 'body': speech(desk, data.get('text', ''), owner,str(data.get('matter') or ''))}
    if name.startswith('m567/config/'):
        if role != 'administrateur':raise Stop('role_insuffisant')
        from .config567 import handle
        return json_out(handle(desk,name,data,env,method))
    raise Stop('route_inconnue')


def cards(rows, prefix):
    out = ''
    for row in rows:
        result, job = row.get('result') or {}, row.get('job') or {}
        out += '<article class="mission567-card" data-mission-id="' + e(row['id']) + '"><div class="mission567-state">' + e(row['label']) + '</div><h3>' + e(row['instruction']) + '</h3>'
        out += '<p>' + e(row['matter_label'] or 'Cabinet') + '</p><p class="muted">' + e(job.get('progress') or row['plan']['deliverable']) + '</p>'
        if result.get('text'):
            out += '<details><summary>Lire la réponse et ses sources</summary><pre>' + e(result['text']) + '</pre></details>'
        if result.get('open_url'):
            out += '<a class="btn" href="' + e(result['open_url']) + '">' + ('Relire le brouillon' if row['kind'] == 'mail' else 'Ouvrir le projet') + '</a>'
        if row['state'] == 'decision':
            choices = {c['id']: c['label'] for x in row['exceptions'] for c in x.get('candidates', [])}
            out += '<p>' + e(' '.join(x['message'] for x in row['exceptions'])) + '</p>'
            if choices:
                out += '<label>Dossier<select class="mission567-choice">' + ''.join('<option value="' + e(k) + '">' + e(v) + '</option>' for k, v in choices.items()) + '</select></label><button type="button" data-mission-control="resolve">Préciser et démarrer</button>'
        if row['state'] == 'error':
            out += '<p class="warn">' + e(job.get('error') or ' '.join(x.get('message', '') for x in row['exceptions'])) + '</p>'
        if row['state'] in ('suggested','error'):
            out += '<button type="button" data-mission-control="resume">'+('Démarrer la préparation' if row['state']=='suggested' else 'Relancer')+'</button>'
        if row['state'] in ('queued', 'running', 'creating', 'paused'):
            out += '<details><summary>Autres actions</summary><button type="button" data-mission-control="' + ('pause' if row['state'] in missions567.ACTIVE else 'resume') + '">' + ('Suspendre' if row['state'] in missions567.ACTIVE else ('Démarrer la préparation' if row['state']=='suggested' else 'Reprendre')) + '</button></details>'
        out += '<details><summary>Plan, limites et preuve</summary><ol>' + ''.join('<li>' + e(x) + '</li>' for x in row['plan']['steps']) + '</ol><p>' + e(' · '.join(row['plan']['restrictions'])) + '</p>'
        if result.get('proof'):
            out += '<p>Relecture : ' + e(str(result['proof'])) + '</p>'
        out += '</details></article>'
    return out or '<p class="notice">Confiez une première mission avec le bouton robot. Son contexte et son état seront conservés ici.</p>'


def page(desk, auth, prefix, env):
    from .web440 import shell
    owner, role = actor(env)
    rows = missions567.listing(desk, owner, role == 'administrateur', prefix)
    body = '<section class="mission567-heading"><h1>Vos missions</h1><p>Donnez le résultat attendu. AxiorHub prépare les travaux internes et vous signale les points à décider.</p><button type="button" data-mission-open>Confier une mission</button><button type="button" data-briefing567>Écouter le briefing</button><a href="' + e(prefix) + '/engagements">Engagements et suites automatiques</a><a href="' + e(prefix) + '/parametres/proactivite">Régler les initiatives</a></section>'
    body += '<p id="mission567-page-status" role="status"></p><div id="mission567-list" class="mission567-grid">' + cards(rows, prefix) + '</div>'
    return shell('Missions', body, prefix, auth['csrf'], '/aujourdhui')


def preferences_page(desk, auth, prefix, env, shell=None):
    from .web440 import shell as default_shell   # 5.6.21
    shell = shell or default_shell
    owner, _ = actor(env)
    p = assistant567.profile(desk, owner)
    def opts(values, current):
        return ''.join('<option value="' + e(key) + '"' + (' selected' if current == key else '') + '>' + e(label) + '</option>' for key, label in values)
    body = '<h1>Voix et comportement</h1><p>La personnalisation règle la manière de travailler. L’assistant reste identifié comme une IA ; elle ne change pas les permissions ni la politique de confidentialité.</p>'
    body += '<form id="assistant567-preferences"><label>Nom affiché<input name="name" maxlength="40" value="' + e(p['name']) + '"></label>'
    body += '<label>Ton<select name="tone">' + opts([('sobre', 'Sobre'), ('direct', 'Direct'), ('pedagogique', 'Pédagogique')], p['tone']) + '</select></label>'
    body += '<label>Initiative<select name="initiative">' + opts([('preparer','Préparer les missions internes'),('proposer','Proposer le plan sans lancer')],p['initiative']) + '</select></label>'
    body += '<label>Longueur<select name="length">' + opts([('courte', 'Courte'), ('developpee', 'Développée')], p['length']) + '</select></label>'
    body += '<label><input type="checkbox" name="speech_enabled"' + (' checked' if p['speech_enabled'] else '') + '> Lecture vocale à la demande</label><label><input type="checkbox" name="discreet"' + (' checked' if p['discreet'] else '') + '> Briefing discret : ne pas prononcer les noms de clients</label>'
    body += '<label>Vitesse<input name="speech_rate" type="number" min="0.7" max="1.5" step="0.1" value="' + str(p['speech_rate']) + '"></label>'
    lexicon = '\n'.join(x['heard'] + ' = ' + x['written'] for x in p['lexicon'])
    body += '<label>Lexique de dictée (forme entendue = forme écrite)<textarea name="lexicon" rows="5">' + e(lexicon) + '</textarea></label><p>Les corrections restent visibles et modifiables avant le démarrage. Les nombres, dates et références ne sont pas remplacés par le lexique.</p><button>Enregistrer mes préférences</button><p id="assistant567-preferences-status" role="status"></p></form>'
    body += '<section><h2>Accueil téléphonique et WhatsApp</h2><p>Accueil Twilio par touches ou conversationnel (l’appelant dit l’objet de son appel : rappel, document, rendez-vous ou message ; deux questions au plus) et réception WhatsApp Business Cloud : demandes administratives transformées en tâches vérifiées. L’annonce lue à l’appelant (accueil automatisé avec IA, transcription Twilio, aucun enregistrement, aucun conseil juridique, touche 0 pour le clavier) se modifie dans Paramètres › Connexions › Accueil administratif. Désactivés jusqu’à configuration des comptes officiels et des signatures. Aucun conseil juridique, aucune consultation de dossier ni réponse WhatsApp automatique.</p></section>'
    body += '<a href="'+e(prefix)+'/parametres/proactivite">Initiatives, engagements, Talk et veille</a> · <a href="'+e(prefix)+'/accueil-administratif">Ouvrir les demandes reçues</a>'
    return shell('Voix et comportement', body, prefix, auth['csrf'], '/parametres')


def connections_page(desk,auth,prefix,env,shell=None,groups=None,exclude=(),tests=None,heading=None):
    from .web440 import shell as default_shell   # 5.6.21 : rendu injectable (concentrateur Paramètres), groupes filtrables
    shell=shell or default_shell
    from .config567 import catalog
    _,role=actor(env)
    if role!='administrateur':raise Stop('role_insuffisant')
    data=catalog(desk,env)
    if groups is not None or exclude:
        data={**data,'fields':[f for f in data['fields'] if (groups is None or f['group'] in groups) and f['group'] not in exclude]}
    body='<h1>'+e(heading or 'Connexions et configuration')+'</h1><p>Les secrets existants ne sont jamais affichés. Une valeur vide conserve le secret. Les paramètres Docker demandent un plan de déploiement et un redémarrage.</p><form id="connections567-form">'
    groups={}
    for field in data['fields']:groups.setdefault(field['group'],[]).append(field)
    for group,fields in groups.items():
        body+='<details'+(' open' if group in ('Courriels','Nextcloud') else '')+'><summary>'+e(group)+'</summary><div class="mission567-fields">'
        for f in fields:
            key,typ=f['key'],f['type'];value=f['value']
            body+='<label>'+e(f['label'])
            if typ in ('lines','paths','urls'):
                body+='<textarea data-config567="'+e(key)+'" rows="3">'+e('\n'.join(value or []))+'</textarea>'
            elif typ=='bool':
                body+='<input type="checkbox" data-config567="'+e(key)+'"'+(' checked' if value else '')+'>'
            elif typ=='secret':
                body+='<input type="password" data-config567="'+e(key)+'" autocomplete="new-password" placeholder="'+('Secret configuré ; laisser vide pour le conserver' if f['secret_configured'] else 'Nouveau secret')+'">'
            elif typ=='local_dir':   # 5.6.24 : bouton de sélection du dossier de travail
                body+=('<span class="browse567-field"><input type="text" data-config567="'+e(key)+'" value="'+e(str(value))+'" placeholder="vide = Nextcloud (WebDAV)">'
                       '<button type="button" class="ghost" data-browse567="'+e(key)+'">📁 Choisir…</button></span>')
            else:
                body+='<input type="'+('number' if typ in ('integer','port') else 'text')+'" data-config567="'+e(key)+'" value="'+e(str(value))+'">'
            body+='</label>'
        body+='</div></details>'
    body+='<button>Enregistrer les changements</button><p id="connections567-status" role="status"></p></form><section><h2>Tests de connexion</h2><p>Enregistrez d’abord vos changements. Les tests ne créent ni facture ni courriel et ne sélectionnent pas vos agendas à votre place.</p>'
    for name,label in [x for x in (('imap','IMAP'),('nextcloud','Nextcloud / agendas'),('ollama','Ollama'),('invoice_ninja','Invoice Ninja'),('voice','Voix locale')) if tests is None or x[0] in tests]:
        body+='<button type="button" data-connector567="'+name+'">Tester '+label+'</button> '
    body+='</section><details><summary>Variables Docker et plan de déploiement</summary><p>Ces variables sont inventoriées. Les secrets ne sont pas exportés ; appliquer ce plan reste une opération d’administration du serveur.</p><table><thead><tr><th>Variable</th><th>Valeur d’exemple / nouveau choix</th><th>Application</th></tr></thead><tbody>'
    for item in data['infrastructure']:
        body+='<tr><td>'+e(item['name'])+'</td><td>'+('Secret à configurer dans le coffre' if item['secret'] else '<input data-docker567="'+e(item['name'])+'" value="'+e(item['example'])+'" aria-label="'+e(item['name'])+'">')+'</td><td>Redémarrage requis</td></tr>'
    body+='</tbody></table><button type="button" data-docker-plan567>Préparer le plan .env expurgé</button><pre id="docker567-plan"></pre></details>'
    body+='<script type="application/json" id="config567-catalog">'+json.dumps(data,ensure_ascii=False).replace('<','\\u003c')+'</script>'
    return shell('Connexions',body,prefix,auth['csrf'],'/parametres')


def reception_page(desk, auth, prefix, env):
    from .web440 import shell
    from .reception567 import listing
    body='<h1>Accueil administratif</h1><p>Le numéro appelant ne constitue pas une preuve d’identité. Les demandes de conseil ou de contenu de dossier restent réservées à l’accueil humain.</p>'
    for r in listing(desk):
        body+='<article class="mission567-card"><h2>'+e(r['channel']+' — '+(r['caller'] or 'coordonnées purgées'))+'</h2><p>'+e(r['at'])+'</p><pre>'+e(r['text'] or 'Message purgé selon la rétention configurée.')+'</pre><p>Tâche locale relue : '+e(r['task_id'])+'</p><a href="'+e(prefix)+'/taches">Traiter dans les tâches</a></article>'
    body+='<p>Aucun enregistrement audio. Les frais de téléphone et de WhatsApp sont facturés par leurs fournisseurs ; AxiorHub ne les a pas mesurés et ne les présente pas comme nuls.</p>'
    return shell('Accueil administratif',body,prefix,auth['csrf'],'/accueil-administratif')
