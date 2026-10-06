"""Interface de configuration, sans exécution de texte ni secrets dans le HTML."""
from html import escape as e
import json
from urllib.parse import urlencode

from .common import Stop,load_matters,matter_display
from . import automation568 as rules,calendar568 as calendars,procedure568,settings568
from .web567 import actor

MESSAGES={
 'date_creation_invalide':'La date doit comprendre son fuseau (exemple : 2026-10-06T10:00:00+02:00).',
 'google_oauth_non_configure':'Dans Connexions, renseignez le client OAuth Google, son secret et l’URI de retour exacte.',
 'google_agenda_non_connecte':'Autorisez d’abord Google Calendar depuis cette page.',
 'regle_inactive_ou_modifiee':'La règle est suspendue ou a changé. Relisez sa nouvelle version avant activation.',
 'dossier_document_ambigu':'Plusieurs dossiers sont possibles. Choisissez le bon dossier avant reprise.',
 'agenda_depot_incertain_ou_supprime_verifier_avant_relance':'Le dépôt est incertain ou a été supprimé. Vérifiez l’agenda ; AxiorHub évite de recréer un événement à l’aveugle.',
 'document_pages_non_extraites_ocr_requis':'Certaines pages n’ont pas de texte exploitable. Configurez l’OCR avant reprise.',
 'autonomie_agent_suspendue':'Le dossier, le profil ou le cabinet est en pause ou en mode observation.',
 'regle_modifiee_recharger':'La règle a été modifiée ailleurs. Rechargez sa dernière version.',
 'autorisation_agenda_google_requise':'Autorisez explicitement la transmission vers cet agenda Google.',
 'agenda_google_dossier_ou_transmission_interdite':'La politique du dossier interdit la transmission vers Google.',
}


def api(env,desk,auth,prefix,route,args,method):
    from .web440 import json_out,check_post
    owner,role=actor(env)
    if role not in ('administrateur','avocat'):raise Stop('role_insuffisant')
    if method=='GET':
        if route=='rules/list':return json_out({'rules':rules.listing(desk,owner),'actions':rules.LABELS,'types':sorted(rules.TYPES)})
        if route=='document/state':return json_out({'runs':rules.runs(desk,owner,prefix)})
        if route=='rules/job':
            try:ident=int(args.get('id',0))
            except (TypeError,ValueError):raise Stop('travail_invalide') from None
            row=desk.db.execute('SELECT * FROM jobs WHERE id=?',(ident,)).fetchone()
            if not row or row['kind'] not in ('document568_compile','document568_scan') or json.loads(row['args']).get('owner')!=owner:raise Stop('travail_invalide')
            return json_out({'id':ident,'state':row['status'],'progress':row['progress'],'result':json.loads(row['result'] or '{}')})
        if route=='calendars/list':return json_out({'targets':calendars.targets(desk,owner),'effective':[{k:v for k,v in x.items() if k!='config'} for x in calendars.effective_targets(desk,owner)],'google_connected':bool(desk.settings('calendar568:google:'+owner,{}))})
        if route=='procedure/get':
            mid=str(args.get('matter') or '')
            if mid not in {str(m['id']) for m in load_matters(desk.c)}:raise Stop('dossier_absent')
            return json_out(procedure568.case_profile(desk,mid))
        if route=='google/callback':
            # Callback OAuth protégé par l'état unique lié à la session ; l'URL
            # contenant le code disparaît immédiatement, sans ressource tierce.
            try:calendars.oauth_finish(desk,owner,args);outcome='connected'
            except Stop as ex:
                desk.audit('google_calendar568_refuse',{'owner':owner,'reason':str(ex)[:120]});outcome='failed'
            return {'status':'303 See Other','kind':'text/plain; charset=utf-8','body':'Retour aux agendas.',
                'headers':[('Location',prefix+'/parametres/agendas?'+urlencode({'google':outcome})),('Referrer-Policy','no-referrer')]}
        raise Stop('route_inconnue')
    if method!='POST':raise Stop('methode_refusee')
    data=check_post(env,auth)
    if route=='rules/compile':
        text=str(data.get('instruction') or '')
        if not 10<=len(text.strip())<=6000:raise Stop('instruction_agent_invalide')
        return json_out({'job':desk.enqueue('document568_compile',{'instruction':text,'owner':owner},priority=5),'message':'Interprétation en cours ; aucune action sur vos services.'})
    if route=='rules/save':return json_out(rules.save(desk,data,owner))
    if route=='rules/control':return json_out(rules.control(desk,data,owner))
    if route=='rules/simulate':
        recipe=rules.validate_recipe(data.get('recipe'));path=str(data.get('filename') or '')[:300]
        metadata={'created':str(data.get('created') or ''),'modified':str(data.get('modified') or '')}
        text=str(data.get('text') or '')[:30000]
        return json_out({'match':rules.match(recipe,path,metadata,text),'document_type':rules.classify(text),'interpretation':rules.recipe_text(recipe),
          'message':'Simulation du déclencheur sur cet exemple ; aucun accès distant ni fichier, événement ou brouillon créé.'})
    if route=='document/control':return json_out(rules.run_control(desk,data,owner))
    if route=='document/scan':
        if not settings568.profile(desk,owner)['enabled']:raise Stop('proactivite_desactivee')
        return json_out({'jobs':[desk.enqueue('live_documents430',{},priority=10),desk.enqueue('document568_scan',{'owner':owner},priority=10)],'message':'Surveillance documentaire lancée ; les exécutions apparaîtront ici.'})
    if route=='calendars/save':return json_out(calendars.save_target(desk,data,owner))
    if route=='calendars/test':return json_out(calendars.diagnostic(desk,owner,str(data.get('id') or '')))
    if route=='google/start':return json_out(calendars.oauth_start(desk,owner))
    if route=='google/disconnect':return json_out(calendars.disconnect(desk,owner))
    if route=='procedure/save':return json_out(procedure568.save_case(desk,data))
    raise Stop('route_inconnue')


def _matters(desk,empty='Tous les dossiers'):
    return '<option value="">'+e(empty)+'</option>'+''.join('<option value="'+e(str(m['id']))+'">'+e(matter_display(m))+'</option>' for m in load_matters(desk.c))


def page(desk,auth,prefix,env,path,args):
    from .web440 import shell
    owner,role=actor(env)
    if role not in ('administrateur','avocat'):raise Stop('role_insuffisant')
    if path=='/parametres/agendas':return agendas(desk,auth,prefix,args)
    if path=='/agents-documents':
        body='<h1>Agents documentaires : résultats et reprises</h1><p>Chaque étape conserve sa preuve. Une écriture incertaine est recherchée avant toute nouvelle tentative.</p><div class="actions"><button type="button" data-doc568-scan>Vérifier les documents</button><a href="'+e(prefix)+'/parametres/agents">Modifier mes agents</a><a href="'+e(prefix)+'/parametres/agendas">Agendas et procédure</a></div><p id="doc568-status" role="status"></p><div id="doc568-runs"></div>'
        return shell('Agents documentaires',body,prefix,auth['csrf'],'/production')
    body='<h1>Créer un agent en langage naturel</h1><p>Décrivez le déclencheur et le travail attendu. AxiorHub interprète une recette limitée aux outils du cabinet. Vous la relisez et l’activez une fois ; les missions suivantes respectent ce périmètre.</p><div class="actions"><a href="'+e(prefix)+'/agents-documents">Résultats et incidents</a><a href="'+e(prefix)+'/parametres/agendas">Agendas, destinataires et délais</a><a href="'+e(prefix)+'/parametres/proactivite">Autonomie du profil</a><a href="'+e(prefix)+'/parametres/connexions">Accès Nextcloud, racines et dossiers d’arrivée</a></div><p id="doc568-status" role="status"></p>'
    body+='<section class="doc568-editor"><form id="doc568-rule"><input name="id" type="hidden"><input name="revision" type="hidden"><label>Nom de l’agent<input name="name" maxlength="160" required placeholder="Assistant procédure"></label><label>Mission ou règle<textarea name="instruction" rows="7" maxlength="6000" required placeholder="Si un nouveau fichier de moins de 10 jours contient AVIS DE RENVOI, analyse-le, classe-le dans PROCEDURE du bon dossier, inscris les dates prouvées dans mes agendas sans doublon et prépare un brouillon d’information selon mon rôle."></textarea></label><button type="button" id="doc568-compile">Interpréter la mission</button><p id="doc568-compile-status" role="status"></p><fieldset id="doc568-recipe" hidden><legend>Déclencheur et effets interprétés</legend><p id="doc568-warnings"></p><div class="mission567-fields"><label>Mots du nom (un par ligne)<textarea name="names" rows="3"></textarea></label><label>Types reconnus<select name="types" multiple size="5">'+''.join('<option value="'+x+'">'+e(x.replace('_',' '))+'</option>' for x in sorted(rules.TYPES))+'</select></label><label>Ancienneté maximale en jours<input name="age_days" type="number" min="1" max="90" value="10"></label><label>Date utilisée<select name="clock"><option value="created">Création WebDAV prouvée</option><option value="modified">Dernière modification WebDAV</option></select></label><label>Portée<select name="scope">'+_matters(desk)+'</select></label><label>Autonomie maximale<select name="mode"><option value="observe">Analyser et proposer</option><option value="prepare">Préparer brouillons et tâches</option><option value="organize">Classer et inscrire également</option></select></label><label>Destinataire du projet<select name="recipient"><option value="case_role">Selon mon rôle au dossier</option><option value="self">Moi uniquement</option></select></label><label>Sous-dossier de classement<select name="folder">'+''.join('<option value="'+f+'">'+f+'</option>' for f in rules.FOLDERS)+'</select></label></div><div id="doc568-actions">'+''.join('<label class="proactive568-check"><input name="actions" type="checkbox" value="'+a+'"'+(' checked disabled' if a=='analyze' else '')+'>'+e(label)+'</label>' for a,label in rules.LABELS.items())+'</div><p>Aucun envoi de courriel, signature, dépôt judiciaire, suppression, écrasement ou script arbitraire. Le niveau du profil et les exclusions restent prioritaires.</p><label class="proactive568-check"><input name="approved" type="checkbox" required>J’ai relu ce déclencheur et ces effets.</label><div class="actions"><button type="submit">Enregistrer et activer</button><button type="button" id="doc568-new">Nouvel agent</button></div><details><summary>Simuler sur un exemple</summary><label>Nom du fichier<input name="sample_filename" placeholder="AVIS_DE_RENVOI.pdf"></label><label>Création ou modification prouvée (ISO avec fuseau)<input name="sample_date" placeholder="2026-10-06T10:00:00+02:00"></label><label>Texte de l’exemple<textarea name="sample_text" rows="4"></textarea></label><button type="button" id="doc568-simulate">Tester le déclencheur sans agir</button><p id="doc568-simulation" role="status"></p></details></fieldset></form></section><section><h2>Mes agents et règles par défaut</h2><p>Les cinq modèles initiaux sont à relire avant leur première activation. Vous pouvez les modifier, suspendre ou supprimer. Une modification suspend la version précédente.</p><div id="doc568-rules"></div></section>'
    return shell('Agents et règles',body,prefix,auth['csrf'],'/parametres')


def agendas(desk,auth,prefix,args):
    from .web440 import shell
    msg=('Google autorisé. Sélectionnez les agendas à alimenter.' if args.get('google')=='connected' else ('Autorisation Google non terminée. Vérifiez le client, l’URI de retour, les droits et recommencez.' if args.get('google')=='failed' else ''))
    body='<h1>Agendas multiples et profil procédural</h1><p>Les agents peuvent inscrire les événements dans chaque agenda activé. Ils vérifient les événements existants avant écriture, puis relisent les créations.</p><a href="'+e(prefix)+'/parametres/connexions">Configurer Nextcloud et le client OAuth Google</a> · <a href="'+e(prefix)+'/parametres/agents">Mes agents</a><p id="doc568-status" role="status">'+e(msg)+'</p><section><h2>Google Calendar</h2><p>Autorisation OAuth : AxiorHub ne demande pas votre mot de passe Google. Les jetons de renouvellement sont stockés dans le coffre local. Par défaut, Google reçoit un rappel minimal : référence du dossier, catégorie et date ; aucun document ni invité.</p><div class="actions"><button type="button" data-google568-connect>Autoriser Google Calendar</button><button type="button" data-google568-discover>Lister mes agendas Google</button><button type="button" data-google568-disconnect>Révoquer Google Calendar</button></div><div id="doc568-google-list"></div></section><section><h2>Agendas destinataires</h2><div id="doc568-calendars"></div><form id="doc568-calendar"><input name="id" type="hidden"><label>Nom affiché<input name="label" required maxlength="120"></label><label>Fournisseur<select name="provider"><option value="nextcloud">Nextcloud (accès déjà configuré)</option><option value="caldav">Autre CalDAV</option><option value="google">Google Calendar</option></select></label><div class="mission567-fields"><label>Adresse complète CalDAV<input name="url" type="url" placeholder="https://cloud.example.com/remote.php/dav/calendars/utilisateur/personnel/"></label><label>Identifiant pour un autre CalDAV<input name="username" autocomplete="username"></label><label>Mot de passe d’application pour un autre CalDAV<input name="password" type="password" autocomplete="new-password" placeholder="Vide : conserver le secret existant"></label><label>Identifiant Google Calendar<input name="calendar_id" value="primary"></label></div><label class="proactive568-check"><input name="enabled" type="checkbox" checked>Activer cette cible</label><label class="proactive568-check"><input name="external_approved" type="checkbox">J’autorise la transmission de ces rappels à Google</label><label class="proactive568-check"><input name="include_details" type="checkbox">Inclure également le titre et l’extrait procédural dans Google (données de dossier)</label><button>Enregistrer l’agenda</button><button type="button" id="doc568-calendar-new">Ajouter une autre cible</button></form><p>Les agendas Nextcloud définis dans Connexions sont utilisés par défaut. Pour en désactiver un, ajoutez sa même adresse ici et décochez Activer. Le bouton Tester vérifie la lecture, sans créer d’événement de test.</p></section>'
    body+='<section><h2>Rôle et procédure par dossier</h2><p>Le nom CONVOC ne suffit pas pour choisir le circuit. Renseignez une fois les informations du dossier. Un point de départ absent, une majoration ou un incident laisse le délai en proposition, sans inscription automatique.</p><form id="doc568-case"><label>Dossier<select name="matter" required>'+_matters(desk,'Sélectionner un dossier')+'</select></label><div class="mission567-fields"><label>Nom de l’avocat utilisateur<input name="lawyer_name"></label><label>Intervention<select name="role"><option value="">À préciser</option><option value="direct">Avocat du client</option><option value="plaidant">Plaidant</option><option value="demandeur">Demandeur</option><option value="defendeur">Défendeur</option><option value="conseil">Conseil</option><option value="postulant">Postulant</option><option value="correspondant">Correspondant</option></select></label><label>Dominus litis : nom<input name="partner_name"></label><label>Dominus litis : adresse<input name="partner_email" type="email"></label><label>Circuit d’appel<select name="circuit"><option value="unknown">À préciser</option><option value="long">Circuit long</option><option value="bref">Bref délai</option></select></label><label>Partie représentée<select name="side"><option value="unknown">À préciser</option><option value="appelant">Appelant</option><option value="intime">Intimé</option></select></label><label>Date de déclaration d’appel<input name="declaration_date" type="date"></label><label>Majorations territoriales<select name="distance"><option value="unknown">À contrôler</option><option value="none">Aucune, après contrôle</option><option value="outre_mer">Outre-mer : contrôle requis</option><option value="etranger">Étranger : contrôle requis</option></select></label><label>L’intimé a-t-il constitué avocat ?<select name="opponent_constituted"><option value="unknown">À préciser</option><option value="yes">Oui</option><option value="no">Non</option></select></label></div><label class="proactive568-check"><input name="ordinary_civil" type="checkbox">Procédure civile ordinaire avec représentation obligatoire</label><label class="proactive568-check"><input name="no_interruption_or_shortening" type="checkbox">Absence vérifiée d’interruption, suspension, réduction judiciaire ou régime particulier</label><label class="proactive568-check"><input name="confirmed" type="checkbox">Profil procédural contrôlé pour ces automatismes</label><button>Enregistrer le profil du dossier</button></form><p>Les dates extraites conservent leur page et leur citation. Les dates d’un PDF Word non paginé sont repérées par sections logiques. Un scan OCR ou un délai non prouvé demande votre contrôle.</p></section>'
    return shell('Agendas et procédure',body,prefix,auth['csrf'],'/parametres')
