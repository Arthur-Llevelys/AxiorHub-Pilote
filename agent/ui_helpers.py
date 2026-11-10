from html import escape
import json
import re


EXAMPLES = {
    '/taches': ['Priorise mes tâches de la semaine et signale les blocages.', 'Prépare les éléments nécessaires pour cette tâche.'],
    '/agenda': ['Prépare mes rendez-vous des sept prochains jours.', 'Repère les échéances sans dossier associé.'],
    '/mail': ['Résume ce courriel et propose les prochaines actions.', 'Prépare un projet de réponse prudent à relire.'],
    '/audiences-word': ['Prépare une trame de plaidoirie à partir des dernières conclusions.', 'Compare nos demandes avec celles de la partie adverse.'],
    '/qualite': ['Explique les principaux motifs d’échec et propose trois corrections.', 'Quels contrôles sont indisponibles ou incomplets ?'],
    '/regles': ['Propose une règle de tri sans l’enregistrer.', 'Explique les effets des règles actives.'],
    '/mcp': ['Quels connecteurs sont actifs et pour quelles fonctions ?', 'Quels connecteurs doivent être testés ?'],
}


def _actions_ia():
    """5.6.89 : drapeau ``actions_ia``. Illisible : le panneau reste celui d'avant."""
    try:
        from .shell501 import drapeaux
        return bool(drapeaux().get('actions_ia'))
    except Exception:   # noqa: BLE001 — l'assistant s'affiche toujours
        return False


def _fonction_page(path):
    """Fonction de la page réellement affichée, onglet compris ; à défaut, celle de l'entrée de menu active."""
    from urllib.parse import parse_qsl
    from .fonctions5631 import FUNCTIONS, for_path
    from .shell501 import _ctx
    chemin = getattr(_ctx, 'chemin', '') or path
    args = getattr(_ctx, 'args', None) or {}
    for row in FUNCTIONS:
        base, _, requete = row[2].partition('?')
        if requete and base == chemin and all(args.get(k) == v for k, v in parse_qsl(requete)):
            return row[0]
    return for_path(chemin, args) or for_path(path, args)


def _contexte_page(path, selected_matter):
    """5.6.96 (I04) : dossier et courriel réellement repris par l'assistant sur cette page, tels que v567.js les lit (paramètres visibles de
    l'adresse) ; le serveur revalide tout à l'envoi (droits, dossier réservé)."""
    try:
        from .shell501 import _ctx
        args = getattr(_ctx, 'args', None) or {}
    except Exception:   # noqa: BLE001 — hors requête : aucun contexte
        args = {}
    dossier = selected_matter or str(args.get('matter') or '') or (str(args.get('id') or '') if path in ('/matter', '/fiche', '/chronologie') else '')
    courriel = bool(re.fullmatch(r'[0-9a-f]{64}', str(args.get('key') or '')))
    return dossier, courriel


# 5.6.96 (U06) : pages dont les scripts reprennent la liste des dossiers du volet pour leurs propres choix (v568.js : engagements ;
# rules568.js : reprises des agents documentaires) : la liste complète y reste rendue, comme avant.
LISTE_COMPLETE = ('/engagements', '/agents-documents')


def assistant_dock(prefix, csrf, matters, path, audio_enabled, selected_matter=''):
    # 5.6.96 (U06) : le volet fermé ne porte plus la liste complète des dossiers (des centaines d'options sur chaque page, jamais lues tant
    # que le volet reste fermé) : « Tout le cabinet » et le dossier de la page seulement ; la liste arrive à l'ouverture (v567.js, loadMatters).
    dossier, courriel = _contexte_page(path, selected_matter)
    options = ['<option value="">Tout le cabinet</option>']
    libelle_dossier = ''
    from .common import matter_display
    try:   # adresse réellement servie (le menu actif d'une page peut être une autre rubrique : /engagements est rangée sous Produire)
        from .shell501 import _ctx as _requete
        complete = path in LISTE_COMPLETE or getattr(_requete, 'chemin', '') in LISTE_COMPLETE
    except Exception:   # noqa: BLE001 — hors requête : l'adresse donnée suffit
        complete = path in LISTE_COMPLETE
    for matter in matters or []:
        mid = str(matter.get('id', ''))
        if mid and mid == dossier:
            libelle_dossier = matter_display(matter)
        if mid and (mid == dossier or complete):
            options.append('<option value="'+escape(mid, quote=True)+'"'+(' selected' if mid == dossier else '')+'>'+escape(matter_display(matter))+'</option>')
    examples=EXAMPLES.get(path, ['Analyse cette page et propose les prochaines actions utiles.',
      'Recherche dans le cabinet les sources utiles à ma demande.'])
    chips=''.join('<button type="button" class="ws-ai-example" data-ai-example="'+escape(x,quote=True)+'">'+escape(x)+'</button>' for x in examples)
    vague = ('<svg class="w79" viewBox="0 0 24 24" aria-hidden="true" fill="currentColor"><rect x="2" y="9" width="2.4" height="6" rx="1.2"/><rect x="6.4" y="6" width="2.4" height="12" rx="1.2"/>'
             '<rect x="10.8" y="3" width="2.4" height="18" rx="1.2"/><rect x="15.2" y="6" width="2.4" height="12" rx="1.2"/><rect x="19.6" y="9" width="2.4" height="6" rx="1.2"/></svg>')
    # 5.6.79 : bouton « vague » de conversation vocale en temps réel quand la dictée locale et une voix locale (Kokoro, eSpeak, Chatterbox)
    # sont réglées ; il ouvre le panneau et démarre le dialogue (v568.js)
    live = ''
    try:
        from .shell501 import context as _ctx5679
        from .voix5694 import fournisseur as _voix94   # 5.6.94 : Brindille (navigateur) par défaut, voix locale aussi
        if audio_enabled and _voix94(_ctx5679() or {}) in ('brindille', 'kokoro', 'espeak', 'chatterbox'):
            live = ('<button type="button" id="ws-ai-live" class="ws-ai-live" aria-pressed="false" title="Conversation vocale en temps réel avec l’assistant" '
                    'aria-label="Conversation vocale en temps réel avec l’assistant">'+vague+'</button>')
    except Exception:   # noqa: BLE001 — sans configuration lisible, pas de bouton
        live = ''
    ia5689=_actions_ia()
    portee_dossier = ('Dossier : '+libelle_dossier) if libelle_dossier else 'Aucun dossier'
    portee_courriel = 'Courriel de cette page' if courriel else 'Aucun courriel'
    # 5.6.96 (U01, I04) : trois zones. En-tête fixe (titre unique, dossier et courriel réellement repris, Changer le contexte, micro actif,
    # agrandir, fermer) ; historique défilant (voix, contexte et actions de la page repliés, plan, fil et résultats, exemples repliés) ;
    # composeur fixe (dossier sur une ligne, pièces jointes, Instruction qui grandit, dictée, Envoyer, documents, conversation vocale, options
    # repliées, état). Les identifiants lus par v567.js, v568.js, v5682.js, v5689-ia.js et v5636.js sont conservés. Volet modal : rôle
    # ``dialog``, fond inerte, focus contenu et rendu à l'ouvreur (v5696-affichage.js) ; la variante montée dans une page reste en flux.
    return (live + '<button type="button" id="ws-ai-launcher" aria-controls="ws-ai-dock" aria-expanded="false" aria-label="Ouvrir l’assistant AxiorHub" '
      'title="Ouvrir l’assistant AxiorHub"><img src="'+escape(prefix, quote=True)+'/static/axiorhub-icon.png" alt="" width="24" height="24" class="ws-ai-logo"></button>'
      '<aside id="ws-ai-dock" class="ws-ai-dock" hidden role="dialog" aria-modal="true" aria-labelledby="ws-ai-titre" aria-describedby="ws-ai-portee">'
      '<header class="ws-ai-tete"><div class="ws-ai-tete-texte"><h2 id="ws-ai-titre">Assistant AxiorHub</h2>'
      '<p id="ws-ai-portee" class="ws-ai-portee"><span id="ws-ai-portee-dossier">'+escape(portee_dossier)+'</span> · <span id="ws-ai-portee-courriel">'+escape(portee_courriel)+'</span>'
      ' <button type="button" id="ws-ai-changer" class="ws-ai-changer" aria-controls="ws-ai-matter-search">Changer le contexte</button></p></div>'
      '<span id="ws-ai-mic" class="ws-ai-mic" hidden role="status"><span class="ws-ai-mic-dot" aria-hidden="true"></span>Micro actif <button type="button" id="ws-ai-mic-stop">Arrêter</button></span>'
      '<button type="button" id="ws-ai-wide" aria-pressed="false" title="Mode élargi pour les analyses longues" aria-label="Mode élargi">⤢</button>'
      '<button type="button" id="ws-ai-close" aria-label="Fermer l’assistant (arrête le micro et la lecture)" title="Fermer">×</button></header>'
      '<div class="ws-ai-body ws-ai-fil" tabindex="-1">'
      '<section id="ws-ai-voice" class="ws-ai-voice" hidden aria-label="Conversation vocale"></section>'
      '<details class="ws-ai-contexte"><summary>Contexte et actions de la page</summary>'
      '<p class="ws-ai-auto-context"><strong>Contexte détecté</strong><br>Page : '+escape(path or '/')+(' · dossier : '+escape(dossier) if dossier else '')+'<br><small>Le dossier, les documents sélectionnés et la dernière mission de ce dossier sont repris. Une ambiguïté sera signalée.</small></p>'
      +('<section id="ia5689-panneau" class="ia89-panneau" aria-label="Contexte et actions de cette page" hidden></section>' if ia5689 else '')+'</details>'
      '<p id="ws-ai-plan" class="ws-ai-plan" role="status" hidden></p>'
      '<section id="ws-ai-result" aria-live="polite"><div id="ws-ai-current"></div><section id="ws-ai-history" class="ws-ai-history" aria-label="Fil de la conversation"></section></section>'
      '<details class="ws-ai-exemples"><summary>Exemples de demandes</summary><div class="ws-ai-examples">'+chips+'</div></details></div>'
      '<footer class="ws-ai-composeur">'
      '<div class="ws-ai-dossier"><label for="ws-ai-matter-search">Dossier facultatif</label><input id="ws-ai-matter-search" type="search" placeholder="Rechercher : client, adversaire, référence…" autocomplete="off" aria-controls="ws-ai-matter">'
      '<select id="ws-ai-matter" aria-label="Dossier de la demande">'+''.join(options)+'</select></div>'
      '<div id="m82-active" class="m82-active" role="status"></div>'
      '<ul id="ws-ai-files" class="ws-ai-files" aria-label="Pièces jointes de la conversation" hidden></ul>'
      '<label class="ws-ai-etiquette" for="ws-ai-question">Instruction</label>'
      '<textarea id="ws-ai-question" rows="2" maxlength="12000" placeholder="Décrivez le résultat attendu…"></textarea>'
      # 5.6.19 : un seul bouton d'envoi, explicite ; les commandes de contexte (chips) sont retirées — l'instruction se dit ou s'écrit.
      '<div class="ws-ai-sendrow"><label class="ws-ai-file">📎 Documents (20 max.)<input type="file" multiple id="ws-ai-file" accept=".pdf,.docx,.txt,.md,.csv,.odt,.eml"></label>'
      '<button type="button" id="ws-ai-talk" class="ws-ai-wave" aria-controls="ws-ai-voice" aria-expanded="false"'+('' if audio_enabled else ' disabled')+' title="Conversation vocale en temps réel : parlez, l’assistant répond à voix haute ; parlez pour l’interrompre">'+vague+'Conversation vocale</button>'
      '<button type="button" id="ws-ai-general" class="ws-ai-general" aria-pressed="false" title="Question générale : sans dossier, sans pièce jointe et sans historique">Général</button>'
      '<button type="button" id="ws-ai-send" class="ws-ai-send">Envoyer l’instruction</button></div>'
      '<p class="ws-ai-hint">Ctrl+Entrée envoie aussi. Pour dicter : le micro sous l’instruction.</p>'
      '<p id="ws-ai-status" role="status">Assistant repliable. Les résultats restent internes et sourcés.</p>'
      '<details class="ws-ai-options"><summary>Options de la mission</summary>'
      '<div class="ws-ai-intent"><label>Résultat attendu<select id="ws-ai-intent"><option value="auto">Laisser le Pilote proposer</option><option value="question">Réponse dans le fil</option>'
      '<option value="analysis">Analyse dans le fil</option><option value="document">Word dans ce dossier</option><option value="mail">Brouillon dans Drafts</option>'
      '<option value="mission">Mission complexe (assignation, conclusions)</option><option value="facturation">Facturation Invoice Ninja (client, projet, devis)</option></select></label></div>'
      '<div class="ws-ai-context"><label>Échéance facultative<input id="ws-ai-due" type="date"></label>'
      '<label>Niveau d’autonomie<select id="ws-ai-autonomy"><option value="prepare">Préparer automatiquement</option><option value="suggest">Proposer seulement</option></select></label></div>'
      '<div class="ws-ai-tools"><button type="button" id="ws-ai-clear" title="Effacer l’affichage de la conversation (les missions restent dans Outils › Missions)">Effacer la conversation</button></div>'
      '<div class="mission567-audio"><button type="button" data-briefing567>Écouter le briefing</button><button type="button" data-read567>Lire la réponse</button><button type="button" data-audio-pause567>Pause / reprise</button><button type="button" data-audio-stop567>Arrêter la lecture</button></div></details>'
      '</footer></aside>'
      '<script type="application/json" id="ws-ai-config">'+json.dumps({'prefix':prefix,'csrf':csrf,'path':path,'selected_matter':dossier,'mission_api':True,'ia5689':ia5689,'fonction':_fonction_page(path) if ia5689 else ''},ensure_ascii=False).replace('</','<\\/')+'</script>')

def versioned(html):
    """5.6.80 : adresses versionnées des scripts et des styles, gardés par le navigateur : 38 fichiers n'ont plus à être rechargés à chaque page,
    et les boutons répondent dès l'affichage. Une nouvelle version change les adresses."""
    from . import __version__
    return re.sub(r'((?:src|href)="[^"?]*/static/[^"?]+\.(?:js|css|woff2))"',lambda m:m.group(1)+'?v='+__version__+'"',html)   # 5.6.96 (U07) : polices aussi


def _police5696(html):
    """5.6.96 (U03) : ``<html data-police>`` d'après le choix de la personne ; une lecture impossible garde la police par défaut."""
    try:
        from .shell501 import context
        from .affichage5696 import compte_courant, marquer_police, police_compte
        return marquer_police(html, police_compte(context() or {}, compte_courant()))
    except Exception:   # noqa: BLE001 — la police ne bloque jamais une page
        return html


def enhance(html,prefix,csrf,audio_enabled,matters=None,path='',selected_matter=''):
    head='<meta name="axiorhub-prefix" content="'+escape(prefix,quote=True)+'"><meta name="axiorhub-csrf" content="'+escape(csrf,quote=True)+'"><meta name="axiorhub-audio" content="'+('enabled' if audio_enabled else 'disabled')+'">'
    from .i18n5631 import current as _lang5634
    from .ecrans5632 import signature as _ecrans5634
    if _lang5634() not in ('fr',''):head+='<meta name="axiorhub-ecrans" content="'+escape(_ecrans5634(_lang5634()),quote=True)+'">'
    head+='<script defer src="'+escape(prefix,quote=True)+'/static/v5680.js"></script>'   # 5.6.80 : très court, le premier exécuté : boutons de l'assistant actifs aussitôt
    head+='<script defer src="'+escape(prefix,quote=True)+'/static/v5682.js"></script>'   # 5.6.82 : état des listes, lecteurs (palette Ctrl+K : v420.js)
    head+='<script defer src="'+escape(prefix,quote=True)+'/static/v5681.js"></script>'   # 5.6.81 : liste des modèles des fournisseurs
    head+='<script defer src="'+escape(prefix,quote=True)+'/static/v5637.js"></script>'   # 5.6.37 : en premier des scripts différés (classe posée avant v320.js)
    head+='<script defer src="'+escape(prefix,quote=True)+'/static/v5634t.js"></script>'   # 5.6.34 : en premier, traduit ce que les scripts écrivent
    head+='<script defer src="'+escape(prefix,quote=True)+'/static/v5694-voix.js"></script>'   # 5.6.94 : lecture (Brindille) et micro (Alkove) communs, avant les dictées
    try:   # 5.6.96 (V06, lot voix) : module d'avatar déclaré seulement si le compte a choisi un portrait (sinon aucune requête)
        from .avatar5696 import script as _avatar5696
        from .i18n5631 import _ctx as _compte5696
        from .shell501 import context as _cfg5696
        head+=_avatar5696(prefix,_cfg5696() or {},getattr(_compte5696,'owner','') or 'cabinet')
    except Exception:   # noqa: BLE001 — sans préférence lisible, pas d'avatar
        pass
    head+='<script defer src="'+escape(prefix,quote=True)+'/static/v310.js"></script>'
    head+=''
    head+='<script defer src="'+escape(prefix,quote=True)+'/static/v320.js"></script>'
    head+='<script defer src="'+escape(prefix,quote=True)+'/static/v330.js"></script>'
    head+='<script defer src="'+escape(prefix,quote=True)+'/static/v360.js"></script>'
    head+='<script defer src="'+escape(prefix,quote=True)+'/static/v363.js"></script>'
    head+='<script defer src="'+escape(prefix,quote=True)+'/static/v364.js"></script>'
    head+='<script defer src="'+escape(prefix,quote=True)+'/static/v365.js"></script>'
    head+='<script defer src="'+escape(prefix,quote=True)+'/static/v370.js"></script>'
    head+='<script defer src="'+escape(prefix,quote=True)+'/static/v5696-affichage.js"></script>'   # 5.6.96 (U01) : volet modal (focus, fond inerte, champ qui grandit, brouillon), avant v567.js
    head+='<script defer src="'+escape(prefix,quote=True)+'/static/v567.js"></script>'
    head+='<script defer src="'+escape(prefix,quote=True)+'/static/v568.js"></script>'
    head+='<script defer src="'+escape(prefix,quote=True)+'/static/v5614.js"></script>'
    head+='<script defer src="'+escape(prefix,quote=True)+'/static/v5625.js"></script>'
    head+='<script defer src="'+escape(prefix,quote=True)+'/static/v5628.js"></script>'   # 5.6.28 : messagerie (répondre, transférer, classer)
    head+='<script defer src="'+escape(prefix,quote=True)+'/static/v5643.js"></script>'   # 5.6.43 : classement dans le dossier client
    head+='<script defer src="'+escape(prefix,quote=True)+'/static/v5647.js"></script>'   # 5.6.47 : recherche dans les listes de dossiers
    head+='<script defer src="'+escape(prefix,quote=True)+'/static/v5652.js"></script>'   # 5.6.52 : routines et connexions
    head+='<script defer src="'+escape(prefix,quote=True)+'/static/v5653.js"></script>'   # 5.6.53 : facturation et honoraires
    head+='<script defer src="'+escape(prefix,quote=True)+'/static/v5651.js"></script>'   # 5.6.51 : file « À décider / À relire », page Dossier
    head+='<script defer src="'+escape(prefix,quote=True)+'/static/v5650.js"></script>'   # 5.6.50 : messagerie interne (pastille, conversations)
    head+='<script defer src="'+escape(prefix,quote=True)+'/static/rules568.js"></script>'
    head+='<script defer src="'+escape(prefix,quote=True)+'/static/v420.js"></script>'
    head+='<script defer src="'+escape(prefix,quote=True)+'/static/v5688-crm.js"></script>'   # 5.6.88 : mentions, note commune, page personnelle
    from .ui_navigation5689 import details_json as _details5689
    head+='<meta name="axiorhub-details" content="'+escape(_details5689(),quote=True)+'">'
    from .affichage5696 import compte_courant as _compte96, empreinte_compte as _empreinte96
    head+='<meta name="axiorhub-compte" content="'+_empreinte96(_compte96())+'">'   # 5.6.96 (U07) : repère court du compte, jamais l'identifiant
    head+='<script defer src="'+escape(prefix,quote=True)+'/static/v5689-formulaires.js"></script>'   # 5.6.89 : FormAdapter, composants Carbon sur champs natifs
    head+='<script defer src="'+escape(prefix,quote=True)+'/static/v5689-navigation.js"></script>'   # 5.6.89 : retour contextuel, panneaux, filtres
    head+='<script defer src="'+escape(prefix,quote=True)+'/static/v5689-ia.js"></script>'   # 5.6.89 : contexte de la page pour l'assistant existant
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
    from .shell501 import drapeaux as _drapeaux5689
    from .ui_drapeaux5689 import marquer as _marquer5689
    html=_marquer5689(html,_drapeaux5689())   # 5.6.89 : le thème entier tient à cet attribut
    html=_police5696(html)   # 5.6.96 (U03) : police choisie par la personne (Figtree par défaut, sans attribut)
    from .live430 import enhance_forms,chrome
    html=enhance_forms(html,prefix)
    html=html.replace('</body>',chrome(prefix)+assistant_dock(prefix,csrf,matters or [],path,audio_enabled,selected_matter)+'</body>',1)
    return versioned(html)
