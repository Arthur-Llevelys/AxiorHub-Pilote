"""5.6.108 (lot K) : parcours P108k du banc ``scripts/parcours-navigateur.py`` (vrai navigateur, données fictives, aucun réseau, aucun modèle).

P108k — conseil de modèles, avec des faux fournisseurs : la détection des modèles locaux est remplacée dans le processus du banc par une liste
fictive (qwen3:32b, gemma3:27b, qwen3.5:9b, llama3.1:8b ; aucun fournisseur d'API), et le faux Ollama du banc répond selon l'étape (réponse
d'un membre, revue croisée en JSON, synthèse du président) :
- Paramètres › IA › « Conseil de modèles » : préréglage « Local équilibré » détecté et appliqué par défaut ; personnalisation (qwen3.5:9b,
  llama3.1:8b, président qwen3.5:9b) enregistrée ; « Revenir au préréglage automatique » ;
- Assistance métier › Examiner les arguments : annonce (membres, appels, durée, coût), case de confirmation, lancement, travail exécuté,
  résultat avec désaccords et réponses individuelles dépliables ;
- volet de l'assistant : option absente pour une question qui n'est pas de jugement, présente sinon ; un clic montre l'annonce et n'envoie
  rien ; « Annuler » n'envoie rien ; « Confirmer » : reçu (numéro de la mission, étape) du lot A, puis « Résultat » ; trace de la mission
  (membres, étapes, désaccords) ;
- 1920 × 950 et 375 × 812 (aucun défilement horizontal), captures.
Non inscrit dans ``PARCOURS`` : l'intégrateur l'inscrit et le lance. Lancement seul : ``python scripts/parcours_p108k.py``.
"""
import json
import re
from pathlib import Path
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
NAV = None
INSTALLES = [{'name': 'qwen3:32b', 'billions': 32.0}, {'name': 'gemma3:27b', 'billions': 27.0}, {'name': 'qwen3.5:9b', 'billions': 9.0},
             {'name': 'llama3.1:8b', 'billions': 8.0}]
QUESTION = 'Faut-il assigner la partie adverse pour le retard d’intervention ou négocier ?'
REVUE = {'non_soutenues': [{'reponse': x, 'point': 'Le délai de 48 heures serait impératif sans réserve (réponse %s).' % x} for x in 'ABC'],
         'references_douteuses': [], 'argument_adverse_manquant': [{'reponse': x, 'point': 'La force majeure invoquée par ZORVAL.'} for x in 'ABC'],
         'accords': ['Le retard est établi.'], 'classement': ['A', 'B']}
SYNTHESE = ('Majeure : la perte est réparée.\nMineure : en l’espèce, l’intervention a eu lieu une semaine après la panne.\nConclusion : '
            'mise en demeure, puis assignation ; réserve : pièces à compléter.\n\nArgument adverse le plus sérieux : la force majeure.\n\n'
            'Points de désaccord du conseil :\n- A conseille d’assigner, B de négocier [C1].\n- Point sans critique.')


def _nav():
    if NAV is not None:
        return NAV
    import __main__ as nav
    return nav


def verifier(condition, message):
    _nav().verifier(condition, message)


def reponse_conseil(origine):
    """Faux Ollama : réponse selon le message système de l'étape du conseil ; tout le reste garde la réponse du banc."""
    def repondre(corps):
        systeme = str(((corps.get('messages') or [{}])[0]).get('content') or '')
        if 'Tu es membre d’un conseil' in systeme:
            return ('Position du membre %s : agir rapidement après mise en demeure. La date de réception de la mise en demeure n’est pas précisée.'
                    % corps.get('model', ''))
        if systeme.startswith('Tu relis les réponses'):
            return json.dumps(REVUE, ensure_ascii=False)
        if systeme.startswith('Tu présides'):
            return SYNTHESE
        if systeme.startswith('Tu relis un texte juridique'):
            return str(corps['messages'][-1]['content']).split('Texte :\n', 1)[-1]
        if systeme.startswith('Tu assistes un avocat'):
            return json.dumps({'texte': 'Réponse fictive du banc.', 'sources_citees': []})
        return origine(corps)
    return repondre


def _sans_defilement(p, lieu):
    verifier(p.evaluate('document.documentElement.scrollWidth <= window.innerWidth + 1'), '%s : défilement horizontal' % lieu)


def _ouvrir(p):
    """La rubrique range chaque section dans un bloc repliable : celui du conseil est ouvert (comme le ferait la personne)."""
    p.evaluate("(() => { const d = document.getElementById('conseil56108').closest('details'); if (d) d.open = true; })()")


def _parametres(s, banc):
    p = s.page
    s.aller('/parametres?rubrique=ia')
    _ouvrir(p)
    section = p.locator('#conseil56108')
    section.scroll_into_view_if_needed()
    texte = section.inner_text()
    ligne = section.locator('tr', has_text='Local équilibré')
    verifier('disponible' in ligne.inner_text() and 'par défaut' in ligne.inner_text(), 'préréglage par défaut non détecté : %r' % ligne.inner_text()[:300])
    verifier('qwen3:32b' in texte and 'gemma3:27b' in texte and 'En vigueur' in texte, 'réglage en vigueur : %r' % texte[:400])
    verifier(':cloud' not in texte, 'un modèle « :cloud » est proposé')
    s.propre('Paramètres › Conseil de modèles')
    section.screenshot(path=str(Path(banc.sortie) / 'P108k-parametres-1920.png'))
    # personnalisation
    p.click('#conseil56108 details.k108-perso > summary')
    p.select_option('#k108-m1', 'ollama::qwen3.5:9b')
    p.select_option('#k108-m2', 'ollama::llama3.1:8b')
    p.select_option('#k108-m3', '')
    p.select_option('#k108-pres', 'ollama::qwen3.5:9b')
    _nav().envoyer(p, 'm56108/conseil/reglages', lambda: p.click('#conseil56108 details.k108-perso button[type=submit]'), 'la personnalisation')
    _ouvrir(p)
    vigueur = p.locator('#conseil56108 p', has_text='En vigueur').inner_text()
    verifier('personnalisé' in vigueur and 'qwen3.5:9b' in vigueur and 'llama3.1:8b' in vigueur, 'personnalisation non appliquée : %r' % vigueur)
    with banc.desk() as d:
        verifier((d.settings('conseil56108:reglages') or {}).get('mode') == 'personnalise', 'personnalisation non enregistrée')
    # retour au préréglage
    _nav().envoyer(p, 'm56108/conseil/reglages', lambda: p.locator('#conseil56108 button', has_text='Revenir au préréglage automatique').click(),
                   'le retour au préréglage')
    _ouvrir(p)
    vigueur = p.locator('#conseil56108 p', has_text='En vigueur').inner_text()
    verifier('Local équilibré' in vigueur and 'qwen3:32b' in vigueur, 'retour au préréglage : %r' % vigueur)
    s.propre('personnalisation et retour au préréglage')


def _arguments(s, banc):
    p = s.page
    s.aller('/assistance-metier?outil=arguments&matter=DOS-001')
    bloc = p.locator('#conseil56108-arguments')
    texte = bloc.inner_text()
    verifier('Annonce' in texte and 'Jusqu’à 6 appels' in texte and 'aucun coût d’API' in texte, 'annonce absente : %r' % texte[:400])
    p.fill('#k108-question', QUESTION)
    p.check('#conseil56108-arguments input[name=confirmer]')
    _nav().envoyer(p, 'm56108/conseil/lancer', lambda: p.click('#conseil56108-arguments button[type=submit]'), 'le lancement du conseil')
    verifier(banc.travailler() >= 1, 'aucun travail exécuté après le lancement')
    s.aller('/assistance-metier?outil=arguments&matter=DOS-001')
    detail = p.locator('#conseil56108-arguments details.k108-detail').first
    verifier('Terminé' in detail.locator(':scope > summary').inner_text(), 'conseil non terminé : %r' % detail.inner_text()[:300])
    detail.locator(':scope > summary').click()
    contenu = detail.inner_text()
    livrable = contenu.split('Désaccords exposés')[0]
    verifier('Majeure' in livrable and 'Points de désaccord du conseil' in livrable and 'A conseille d’assigner, B de négocier.' in livrable,
             'livrable : %r' % livrable[:500])
    verifier('Informations à obtenir' in livrable and 'date de réception de la mise en demeure' in livrable,
             'manque d’un membre non repris en question : %r' % livrable[:600])
    verifier('Point sans critique' not in livrable and 'La force majeure invoquée par ZORVAL.' not in livrable,
             'point inventé ou texte de critique dans le livrable : %r' % livrable[:500])
    critiques = detail.locator('details.k108-detail', has_text='Désaccords exposés').first
    critiques.locator(':scope > summary').click()
    verifier('La force majeure invoquée par ZORVAL.' in critiques.inner_text(), 'texte exact des critiques absent de la trace')
    reponse = detail.locator('details.k108-detail').filter(has=p.locator(':scope > summary', has_text=re.compile(r'^Réponse A'))).first
    verifier(reponse.count() == 1, 'réponse individuelle non dépliable')
    reponse.locator(':scope > summary').click()
    verifier('Position du membre' in reponse.inner_text(), 'réponse individuelle illisible')
    s.propre('Examiner les arguments : conseil terminé')
    s.capture(Path(banc.sortie) / 'P108k-arguments-1920.png')


def _volet(s, banc):
    p = s.page
    s.aller('/aujourdhui')
    p.click('#ws-ai-launcher')
    p.wait_for_selector('#ws-ai-question', state='visible')
    p.select_option('#ws-ai-matter', 'DOS-001')
    bouton = p.locator('#k108-volet')
    p.fill('#ws-ai-question', 'Résume le dernier courriel reçu.')
    p.wait_for_timeout(600)
    verifier(bouton.is_hidden(), 'option présente pour une question qui n’est pas de jugement')
    p.fill('#ws-ai-question', QUESTION)
    bouton.wait_for(state='visible', timeout=10000)
    with banc.desk() as d:
        avant = d.db.execute('SELECT COUNT(*) FROM missions_v567').fetchone()[0]
    bouton.click()
    p.wait_for_selector('.k108-annonce button:has-text("Confirmer")', timeout=10000)
    annonce = p.locator('.k108-annonce').inner_text()
    verifier('Membres' in annonce and 'appels' in annonce and 'durée estimée' in annonce, 'annonce du volet : %r' % annonce[:300])
    p.locator('.k108-annonce button', has_text='Annuler').click()
    p.wait_for_timeout(500)
    with banc.desk() as d:
        verifier(d.db.execute('SELECT COUNT(*) FROM missions_v567').fetchone()[0] == avant, 'une mission a été créée sans confirmation')
    bouton.click()
    with p.expect_response(lambda r: 'm56108/conseil/lancer' in r.url and r.request.method == 'POST') as rep:
        p.locator('.k108-annonce button', has_text='Confirmer').click()
    corps = rep.value.json()
    verifier(rep.value.ok and corps.get('mission'), 'lancement depuis le volet refusé : %r' % corps)
    recu = p.locator('#k108-volet-recu .ax108-recu')
    recu.wait_for(state='visible', timeout=10000)
    verifier('Reçu n°' in recu.inner_text(), 'reçu du lot A absent : %r' % recu.inner_text())
    s.propre('volet : annonce, confirmation, reçu')
    s.capture(Path(banc.sortie) / 'P108k-volet-recu-1920.png')
    banc.travailler()
    p.wait_for_selector('#k108-volet-recu .ax108-recu[data-etape="resultat"]', timeout=20000)
    trace = p.evaluate("(id) => fetch(document.querySelector('meta[name=axiorhub-prefix]').content + '/api440/m56108/conseil/trace?mission=' + "
                       "encodeURIComponent(id), {credentials: 'same-origin'}).then(r => r.json())", corps['mission'])
    t = trace.get('trace') or {}
    verifier(trace.get('etat') == 'termine' and len(t.get('membres') or []) == 2 and t.get('desaccords') and t.get('etapes'), 'trace : %r' % str(trace)[:400])
    s.propre('volet : résultat et trace')
    return corps['mission']


def _mobile(s, banc):
    ctx = s.context.browser.new_context(viewport={'width': 375, 'height': 812}, is_mobile=True, has_touch=True, device_scale_factor=2, locale='fr-FR')
    try:
        cookies = s.context.cookies()
        ctx.add_cookies(cookies)
        m = _nav().Session(banc, ctx)
        m.aller('/parametres?rubrique=ia')
        _ouvrir(m.page)
        m.page.locator('#conseil56108').scroll_into_view_if_needed()
        _sans_defilement(m.page, 'Paramètres à 375 px')
        m.page.locator('#conseil56108').screenshot(path=str(Path(banc.sortie) / 'P108k-parametres-375.png'))
        m.aller('/assistance-metier?outil=arguments&matter=DOS-001')
        _sans_defilement(m.page, 'Examiner les arguments à 375 px')
        m.capture(Path(banc.sortie) / 'P108k-arguments-375.png')
        m.propre('375 px')
    finally:
        ctx.close()


def p108k_conseil(banc, s):
    from agent import conseil56108
    origine = banc.modele.reponse
    banc.modele.reponse = reponse_conseil(origine)
    rustines = [patch.object(conseil56108, '_installes', lambda desk: [dict(x) for x in INSTALLES]),
                patch.object(conseil56108, '_fournisseurs_api', lambda desk: {})]
    for r in rustines:
        r.start()
    try:
        s.connecter()
        s.page.set_viewport_size({'width': 1920, 'height': 950})
        _parametres(s, banc)
        _arguments(s, banc)
        _volet(s, banc)
        _mobile(s, banc)
    finally:
        for r in rustines:
            r.stop()
        banc.modele.reponse = origine


def main(argv=None):
    """Lancement seul (sans inscription dans ``PARCOURS``) : banc chargé comme module, parcours ajouté."""
    global NAV
    import argparse
    import importlib.util
    import tempfile
    parser = argparse.ArgumentParser()
    parser.add_argument('--sortie', default=str(Path(tempfile.gettempdir()) / 'axiorhub-parcours-p108k'))
    parser.add_argument('--navigateur', default='chromium')
    args = parser.parse_args(argv)
    sys.path.insert(0, str(ROOT))
    sys.path.insert(0, str(ROOT / 'scripts'))
    spec = importlib.util.spec_from_file_location('parcours_navigateur', str(ROOT / 'scripts' / 'parcours-navigateur.py'))
    NAV = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(NAV)
    import parcours_p108k   # le banc l'inscrit désormais lui-même : ce module importé reçoit le banc chargé ici
    parcours_p108k.NAV = NAV
    if 'P108k' not in {i for i, _, _ in NAV.PARCOURS}:
        NAV.PARCOURS = NAV.PARCOURS + (('P108k', 'Conseil de modèles (préréglage détecté, personnalisation, Examiner les arguments, volet, annonce, '
                                                 'reçu, désaccords, trace ; 1920 et 375 px)', parcours_p108k.p108k_conseil),)
    return NAV.lancer(args.navigateur, args.sortie, ('P108k',))


if __name__ == '__main__':
    sys.exit(main())
