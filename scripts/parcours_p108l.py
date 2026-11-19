"""5.6.108 (lot L) : parcours P108l du banc ``scripts/parcours-navigateur.py`` (vrai navigateur, données fictives, aucun réseau, aucun modèle).

P108l — « Configurer l'IA locale » avec un faux Ollama de bouclage (``tests/faux_ollama56108.py`` : qwen3:8b, qwen3-embedding:0.6b et un modèle
« -cloud » installés ; téléchargement simulé) et une machine fictive (16 Go de mémoire vive, sans carte graphique : classe légère). Les
adresses de repli (127.0.0.1:11434, localhost) sont retirées pendant le parcours : aucun vrai Ollama n'est jamais appelé.
- Paramètres › IA : Ollama joignable (version), machine « Légère », modèles installés (le modèle « cloud » est dit non local), suggestions
  (contrôle : ministral-3:8b à télécharger, famille différente du rédacteur) ;
- téléchargement : « Télécharger… » n'envoie rien (aucun /api/pull reçu) ; « Annuler » n'envoie rien ; confirmation, progression,
  « Annuler le téléchargement », « Reprendre le téléchargement », fin ; le modèle apparaît dans la liste ;
- réglages : « Appliquer cette configuration » (contrôle ministral-3:8b, préréglage du conseil « Local léger »), essai court (chaque modèle
  répond, durée), « Revenir aux réglages précédents » (réglages rétablis) ;
- adresse publique refusée ; adresse saisie du « réseau » (le faux Ollama) ;
- guide de démarrage, étape « IA » : même assistant ;
- 1920 × 950 et 375 × 812 (aucun défilement horizontal), captures.
Non inscrit dans ``PARCOURS`` : l'intégrateur l'inscrit et le lance. Lancement seul : ``python scripts/parcours_p108l.py``.
"""
import json
from pathlib import Path
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
NAV = None


def _nav():
    if NAV is not None:
        return NAV
    import __main__ as nav
    return nav


def verifier(condition, message):
    _nav().verifier(condition, message)


def _sans_defilement(p, lieu):
    verifier(p.evaluate('document.documentElement.scrollWidth <= window.innerWidth + 1'), '%s : défilement horizontal' % lieu)


def _ouvrir(p):
    p.evaluate("(() => { const s = document.getElementById('ia-locale56108'); const d = s && s.closest('details'); if (d) d.open = true; })()")


def _attendre_texte(p, selecteur, texte, delai=15000):
    p.wait_for_function('([s, t]) => { const n = document.querySelector(s); return !!n && n.innerText.indexOf(t) >= 0; }', arg=[selecteur, texte], timeout=delai)


def _configurer(banc, faux):
    cfg = json.loads(Path(banc.config_path).read_text(encoding='utf-8'))
    cfg['ollama'] = {**cfg['ollama'], 'url': faux.url, 'model': 'qwen3:8b'}
    cfg['model_routing'] = {'fast_model': 'qwen3:8b', 'complex_model': 'qwen3:8b', 'control_model': 'qwen3:8b'}
    cfg['rag'] = {**(cfg.get('rag') or {}), 'enabled': True, 'embedding_model': 'qwen3-embedding:0.6b'}
    from agent.common import private_json
    private_json(Path(banc.config_path), cfg)


def _parametres(s, banc, faux):
    p = s.page
    s.aller('/parametres?rubrique=ia')
    _ouvrir(p)
    _attendre_texte(p, '#l108-detection', 'Ollama joignable')
    det = p.locator('#l108-detection').inner_text()
    verifier('0.12.99' in det and 'Cet ordinateur' in det, 'détection : %r' % det[:300])
    verifier('Légère' in p.locator('#l108-machine').inner_text(), 'classe de machine : %r' % p.locator('#l108-machine').inner_text()[:300])
    installes = p.locator('#l108-installes').inner_text()
    verifier('gpt-oss:120b-cloud' in installes and 'Non local (cloud)' in installes, 'modèle cloud non signalé : %r' % installes[:400])
    sugg = p.locator('#l108-suggestions').inner_text()
    verifier('ministral-3:8b' in sugg and 'Apache 2.0' in sugg and 'registry.ollama.ai' in sugg, 'suggestions : %r' % sugg[:600])
    verifier('gpt-oss' not in p.locator('#l108-appliquer').inner_text(), 'un modèle cloud est proposé dans les réglages')
    s.propre('Paramètres › IA : détection, machine, modèles, suggestions')
    p.locator('#ia-locale56108').screenshot(path=str(Path(banc.sortie) / 'P108l-assistant-1920.png'))


def _telechargement(s, banc, faux):
    p = s.page
    carte = p.locator('#l108-suggestions .l108-carte', has_text='ministral-3:8b').first
    carte.locator('button', has_text='Télécharger…').click()
    conf = p.locator('.l108-confirmation')
    conf.wait_for(state='visible')
    texte = conf.inner_text()
    verifier('ministral-3:8b' in texte and 'Taille du téléchargement' in texte and 'Place libre' in texte and 'Licence' in texte,
             'confirmation incomplète : %r' % texte[:400])
    p.locator('.l108-confirmation').screenshot(path=str(Path(banc.sortie) / 'P108l-confirmation-1920.png'))
    conf.locator('button', has_text='Annuler').click()
    p.wait_for_timeout(400)
    verifier(faux.pulls() == [], 'un téléchargement est parti sans confirmation')
    # confirmation, progression, annulation
    faux.etapes_pull, faux.pause_pull = 40, 0.25
    carte.locator('button', has_text='Télécharger…').click()
    with p.expect_response(lambda r: 'ia-locale/telecharger' in r.url and r.request.method == 'POST') as rep:
        p.locator('.l108-confirmation button', has_text='Confirmer le téléchargement').click()
    verifier(rep.value.ok, 'téléchargement refusé : %r' % rep.value.text()[:300])
    p.wait_for_selector('#l108-telechargement progress', timeout=10000)
    p.wait_for_function("() => { const b = document.querySelector('#l108-telechargement progress'); return b && b.value > 0; }", timeout=15000)
    s.capture(Path(banc.sortie) / 'P108l-progression-1920.png')
    p.locator('#l108-telechargement button', has_text='Annuler le téléchargement').click()
    _attendre_texte(p, '#l108-telechargement', 'Annulé')
    verifier(p.locator('#l108-telechargement button', has_text='Reprendre le téléchargement').count() == 1, 'reprise non proposée après annulation')
    verifier('ministral-3:8b' not in faux.modeles, 'le modèle annulé est installé')
    # reprise jusqu'à la fin
    faux.etapes_pull, faux.pause_pull = 8, 0.1
    p.locator('#l108-telechargement button', has_text='Reprendre le téléchargement').click()
    with p.expect_response(lambda r: 'ia-locale/telecharger' in r.url and r.request.method == 'POST') as rep:
        p.locator('.l108-confirmation button', has_text='Confirmer le téléchargement').click()
    verifier(rep.value.ok, 'reprise refusée')
    _attendre_texte(p, '#l108-telechargement', 'Terminé', 20000)
    _attendre_texte(p, '#l108-installes', 'ministral-3:8b', 20000)
    verifier(len(faux.pulls()) == 2, 'nombre de téléchargements demandés à Ollama : %d' % len(faux.pulls()))
    s.propre('téléchargement confirmé, annulé, repris')


def _reglages(s, banc, faux):
    p = s.page
    _attendre_texte(p, '#l108-appliquer', 'Appliquer cette configuration')
    verifier(p.input_value('#l108-controle') == 'ministral-3:8b', 'contrôle proposé : %r' % p.input_value('#l108-controle'))
    verifier(p.input_value('#l108-conseil') == 'local_leger', 'préréglage du conseil proposé : %r' % p.input_value('#l108-conseil'))
    with p.expect_response(lambda r: 'ia-locale/appliquer' in r.url and r.request.method == 'POST') as rep:
        p.click('#l108-appliquer button[type=submit]')
    corps = rep.value.json()
    verifier(rep.value.ok and corps.get('ok'), 'application refusée : %r' % corps)
    _attendre_texte(p, '#l108-essai', 'Terminé', 20000)
    essai = p.locator('#l108-essai').inner_text()
    verifier(essai.count('Répond') >= 3 and 'Échec' not in essai, 'essai court : %r' % essai[:500])
    with banc.desk() as d:
        verifier(d.settings('model:role:control') == 'ministral-3:8b', 'contrôle non enregistré')
        verifier((d.settings('conseil56108:reglages') or {}).get('prereglage') == 'local_leger', 'préréglage du conseil non enregistré')
    s.propre('configuration appliquée, essai court')
    p.locator('#ia-locale56108').screenshot(path=str(Path(banc.sortie) / 'P108l-applique-1920.png'))
    # retour
    p.once('dialog', lambda dialog: dialog.accept())
    with p.expect_response(lambda r: 'ia-locale/revenir' in r.url and r.request.method == 'POST') as rep:
        p.locator('#l108-retour button', has_text='Revenir aux réglages précédents').click()
    verifier(rep.value.ok, 'retour refusé : %r' % rep.value.text()[:300])
    p.wait_for_timeout(800)
    with banc.desk() as d:
        verifier(not d.settings('model:role:control', ''), 'réglage du contrôle non rétabli')
        verifier((d.settings('conseil56108:reglages') or {}).get('mode') == 'auto', 'conseil non rétabli')
    s.propre('retour aux réglages précédents')


def _adresses(s, banc, faux):
    p = s.page
    p.fill('#l108-adresse-champ', 'http://8.8.8.8:11434')
    p.click('#l108-adresse button[type=submit]')
    _attendre_texte(p, '#l108-detection', 'Adresse refusée')
    s.erreurs[:] = [x for x in s.erreurs if 'status of 400' not in x]       # refus attendu (HTTP 400), rien d'autre
    verifier(not any('8.8.8.8' in str(x) for x in faux.journal), 'adresse publique contactée')
    p.fill('#l108-adresse-champ', faux.url)
    p.click('#l108-adresse button[type=submit]')
    _attendre_texte(p, '#l108-detection', 'Ollama joignable')
    s.propre('adresse du réseau')


def _guide(s, banc):
    p = s.page
    s.aller('/guide-installation')
    p.locator('button.g5631-start[data-mode="verifier"]').click()
    p.wait_for_selector('#g5631[data-session]', timeout=10000)
    session = p.get_attribute('#g5631', 'data-session')
    s.aller('/guide-installation?session=%s&etape=ia' % session)
    _attendre_texte(p, '#l108-detection', 'Ollama joignable')
    verifier(p.locator('#ia-locale56108 h3', has_text='Configurer l’IA locale').count() == 1, 'assistant absent du guide')
    s.propre('guide de démarrage, étape IA')
    s.capture(Path(banc.sortie) / 'P108l-guide-1920.png')


def _mobile(s, banc):
    ctx = s.context.browser.new_context(viewport={'width': 375, 'height': 812}, is_mobile=True, has_touch=True, device_scale_factor=2, locale='fr-FR')
    try:
        ctx.add_cookies(s.context.cookies())
        m = _nav().Session(banc, ctx)
        m.aller('/parametres?rubrique=ia')
        _ouvrir(m.page)
        _attendre_texte(m.page, '#l108-detection', 'Ollama joignable')
        m.page.locator('#ia-locale56108').scroll_into_view_if_needed()
        _sans_defilement(m.page, 'Paramètres › IA à 375 px')
        m.page.locator('#ia-locale56108').screenshot(path=str(Path(banc.sortie) / 'P108l-assistant-375.png'))
        m.propre('375 px')
    finally:
        ctx.close()


def p108l_ia_locale(banc, s):
    sys.path.insert(0, str(ROOT / 'tests'))
    from faux_ollama56108 import FauxOllama, FausseSonde, modele
    from agent import ia_locale56108
    faux = FauxOllama((modele('qwen3:8b', 8.2, 5.2, ('completion', 'tools', 'thinking'), famille='qwen3'),
                       modele('qwen3-embedding:0.6b', 0.6, 0.64, ('embedding',), famille='qwen3'),
                       modele('gpt-oss:120b-cloud', 116.8, 0.0, distant=True)))
    faux.apres_pull['ministral-3:8b'] = modele('ministral-3:8b', 8.9, 6.0, ('completion', 'tools', 'vision'), famille='mistral3')
    rustines = [patch.object(ia_locale56108, 'CANDIDATS', ()),
                patch.object(ia_locale56108, 'Sonde', lambda *a, **k: FausseSonde(plateforme='linux', ram=16.0, coeurs=8, disque=120.0))]
    for r in rustines:
        r.start()
    ia_locale56108._SONDES.clear()
    try:
        _configurer(banc, faux)
        s.connecter()
        s.page.set_viewport_size({'width': 1920, 'height': 950})
        _parametres(s, banc, faux)
        _telechargement(s, banc, faux)
        _reglages(s, banc, faux)
        _adresses(s, banc, faux)
        _guide(s, banc)
        _mobile(s, banc)
    finally:
        for r in rustines:
            r.stop()
        ia_locale56108._SONDES.clear()
        faux.arreter()


def main(argv=None):
    """Lancement seul (sans inscription dans ``PARCOURS``) : banc chargé comme module, parcours ajouté."""
    global NAV
    import argparse
    import importlib.util
    import tempfile
    parser = argparse.ArgumentParser()
    parser.add_argument('--sortie', default=str(Path(tempfile.gettempdir()) / 'axiorhub-parcours-p108l'))
    parser.add_argument('--navigateur', default='chromium')
    args = parser.parse_args(argv)
    sys.path.insert(0, str(ROOT))
    sys.path.insert(0, str(ROOT / 'scripts'))
    spec = importlib.util.spec_from_file_location('parcours_navigateur', str(ROOT / 'scripts' / 'parcours-navigateur.py'))
    NAV = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(NAV)
    import parcours_p108l
    parcours_p108l.NAV = NAV
    if 'P108l' not in {i for i, _, _ in NAV.PARCOURS}:
        NAV.PARCOURS = NAV.PARCOURS + (('P108l', 'Configurer l’IA locale (détection, machine, modèles, suggestions, téléchargement confirmé, '
                                                 'annulé et repris, réglages, essai court, retour, guide ; 1920 et 375 px)', parcours_p108l.p108l_ia_locale),)
    return NAV.lancer(args.navigateur, args.sortie, ('P108l',))


if __name__ == '__main__':
    sys.exit(main())
