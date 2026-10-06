from html import escape
import json


EXAMPLES = {
    '/taches': ['Priorise mes tâches de la semaine et signale les blocages.', 'Prépare les éléments nécessaires pour cette tâche.'],
    '/agenda': ['Prépare mes rendez-vous des sept prochains jours.', 'Repère les échéances sans dossier associé.'],
    '/mail': ['Résume ce courriel et propose les prochaines actions.', 'Prépare un projet de réponse prudent à relire.'],
    '/audiences-word': ['Prépare une trame de plaidoirie à partir des dernières conclusions.', 'Compare nos demandes avec celles de la partie adverse.'],
    '/qualite': ['Explique les principaux motifs d’échec et propose trois corrections.', 'Quels contrôles sont indisponibles ou incomplets ?'],
    '/regles': ['Propose une règle de tri sans l’enregistrer.', 'Explique les effets des règles actives.'],
    '/mcp': ['Quels connecteurs sont actifs et pour quelles fonctions ?', 'Quels connecteurs doivent être testés ?'],
}


def assistant_dock(prefix, csrf, matters, path, audio_enabled, selected_matter=''):
    options=['<option value="">Tout le cabinet</option>']
    for matter in matters or []:
        from .common import matter_display
        label=matter_display(matter)
        mid=str(matter.get('id',''))
        options.append('<option value="'+escape(mid,quote=True)+'"'+(' selected' if mid==selected_matter else '')+'>'+escape(label)+'</option>')
    examples=EXAMPLES.get(path, ['Analyse cette page et propose les prochaines actions utiles.',
      'Recherche dans le cabinet les sources utiles à ma demande.'])
    chips=''.join('<button type="button" class="ws-ai-example" data-ai-example="'+escape(x,quote=True)+'">'+escape(x)+'</button>' for x in examples)
    return ('<button type="button" id="ws-ai-launcher" aria-controls="ws-ai-dock" aria-expanded="false" '
      'title="Ouvrir l’assistant AxiorHub"><span aria-hidden="true">🤖</span><span class="sr-only">Assistant AxiorHub</span></button>'
      '<aside id="ws-ai-dock" class="ws-ai-dock" hidden aria-label="Assistant AxiorHub">'
      '<header><div><strong>Assistant AxiorHub</strong><small>Missions internes · aucun envoi ni engagement automatique</small></div>'
      '<button type="button" id="ws-ai-close" aria-label="Replier l’assistant">×</button></header>'
      '<div class="ws-ai-body"><p class="ws-ai-auto-context"><strong>Contexte détecté</strong><br>Page : '+escape(path or '/')+(' · dossier : '+escape(selected_matter) if selected_matter else '')+'<br><small>Le dossier, les documents sélectionnés et la dernière mission de ce dossier sont repris. Une ambiguïté sera signalée.</small></p><label>Dossier facultatif<select id="ws-ai-matter">'+''.join(options)+'</select></label>'
      '<label>Instruction<textarea id="ws-ai-question" rows="6" maxlength="12000" placeholder="Décrivez le résultat attendu…"></textarea></label>'
      '<details><summary>Options de la mission</summary><div class="ws-ai-context"><label>Échéance facultative<input id="ws-ai-due" type="date"></label>'
      '<label>Niveau d’autonomie<select id="ws-ai-autonomy"><option value="prepare">Préparer automatiquement</option><option value="suggest">Proposer seulement</option></select></label></div></details>'
      '<div class="ws-ai-tools"><button type="button" id="ws-ai-dictate"'+('' if audio_enabled else ' disabled')+'>🎙 Parler</button>'
      '<label class="ws-ai-file">📎 Documents (3 max.)<input type="file" multiple id="ws-ai-file" accept=".pdf,.docx,.txt,.md,.csv,.odt,.eml"></label>'
      '<button type="button" id="ws-ai-send">Démarrer la mission</button></div>'
      '<div class="mission567-audio"><button type="button" data-briefing567>Écouter le briefing</button><button type="button" data-read567>Lire la réponse</button><button type="button" data-audio-pause567>Pause / reprise</button><button type="button" data-audio-stop567>Arrêter la lecture</button></div><p id="ws-ai-status" role="status">Assistant repliable. Les résultats restent internes et sourcés.</p>'
      '<section id="ws-ai-result" aria-live="polite"></section><details><summary>Exemples de demandes</summary><div class="ws-ai-examples">'+chips+'</div></details></div></aside>'
      '<script type="application/json" id="ws-ai-config">'+json.dumps({'prefix':prefix,'csrf':csrf,'path':path,'selected_matter':selected_matter,'mission_api':True},ensure_ascii=False).replace('</','<\\/')+'</script>')

def enhance(html,prefix,csrf,audio_enabled,matters=None,path='',selected_matter=''):
    head='<meta name="axiorhub-prefix" content="'+escape(prefix,quote=True)+'"><meta name="axiorhub-csrf" content="'+escape(csrf,quote=True)+'"><meta name="axiorhub-audio" content="'+('enabled' if audio_enabled else 'disabled')+'">'
    head+='<script defer src="'+escape(prefix,quote=True)+'/static/v310.js"></script>'
    head+=''
    head+='<script defer src="'+escape(prefix,quote=True)+'/static/v320.js"></script>'
    head+='<script defer src="'+escape(prefix,quote=True)+'/static/v330.js"></script>'
    head+='<script defer src="'+escape(prefix,quote=True)+'/static/v360.js"></script>'
    head+='<script defer src="'+escape(prefix,quote=True)+'/static/v363.js"></script>'
    head+='<script defer src="'+escape(prefix,quote=True)+'/static/v364.js"></script>'
    head+='<script defer src="'+escape(prefix,quote=True)+'/static/v365.js"></script>'
    head+='<script defer src="'+escape(prefix,quote=True)+'/static/v370.js"></script>'
    head+='<script defer src="'+escape(prefix,quote=True)+'/static/v567.js"></script>'
    head+='<script defer src="'+escape(prefix,quote=True)+'/static/v568.js"></script>'
    head+='<script defer src="'+escape(prefix,quote=True)+'/static/rules568.js"></script>'
    head+='<script defer src="'+escape(prefix,quote=True)+'/static/v420.js"></script>'
    head+='<meta name="htmx-config" content=\'{"allowEval":false,"allowScriptTags":false,"includeIndicatorStyles":false,"selfRequestsOnly":true}\'>'
    head+='<script defer src="'+escape(prefix,quote=True)+'/static/htmx.min.js"></script><script defer src="'+escape(prefix,quote=True)+'/static/v430.js"></script>'
    head+='<link rel="icon" type="image/png" sizes="150x150" href="'+escape(prefix,quote=True)+'/static/axiorhub-icon.png">'
    head+='<link rel="apple-touch-icon" href="'+escape(prefix,quote=True)+'/static/axiorhub-icon.png">'
    head+='<link rel="manifest" href="'+escape(prefix,quote=True)+'/pwa/manifest.webmanifest" crossorigin="use-credentials"><meta name="theme-color" content="#17324d">'
    head+='<script defer src="'+escape(prefix,quote=True)+'/static/v490.js"></script>'
    head+='<script defer src="'+escape(prefix,quote=True)+'/static/v520.js"></script>'
    if '/static/app520.css' not in html:
        from . import __version__
        head='<link rel="stylesheet" href="'+escape(prefix,quote=True)+'/static/app520.css?v='+escape(__version__,quote=True)+'">'+head
    html=html.replace('</head>',head+'</head>',1)
    from .live430 import enhance_forms,chrome
    html=enhance_forms(html,prefix)
    return html.replace('</body>',chrome(prefix)+assistant_dock(prefix,csrf,matters or [],path,audio_enabled,selected_matter)+'</body>',1)
