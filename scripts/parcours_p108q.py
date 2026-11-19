"""5.6.108 (lot Q) : parcours P108q du banc ``scripts/parcours-navigateur.py`` (vrai navigateur, version serveur du banc, données fictives,
aucun réseau, aucun modèle réel).

P108q-questionnaire :
- fiche du modèle « Contrat de prestation de services » : carte « Questionnaire de l'acte », dossier DOS-001 choisi, « Préparer avec le
  questionnaire » ouvre le formulaire ;
- 1366 × 900 : formulaire et aperçu côte à côte, un seul groupe déplié (le premier utile), « Brouillon partiel » affiché ;
- saisie non enregistrée (Client, Prestataire, « Je ne sais pas » sur un champ) : page rechargée, la saisie revient avec l'avis ;
  « Enregistrer et continuer » : l'aperçu montre les parties, la section indépendante est rédigée, les autres « À compléter » ;
- second onglet resté sur l'ancienne révision : son enregistrement est refusé (409) avec un message, sa saisie reste dans le navigateur ;
- « Télécharger le brouillon (Word) » : fichier « … brouillon partiel.docx » ;
- correction manuelle d'une section : conservée et marquée « Corrigée à la main » ;
- IA en pause : « Faire rédiger par l'IA » annonce la pause, le formulaire reste utilisable ;
- « Dicter la réponse » (micro simulé : reconnaissance du navigateur factice, aucun son) : bouton focusable et nommé ; texte ajouté au
  curseur sans effacer la saisie ; date reconnue non valide affichée pour correction et champ inchangé, date valide posée ; aucune requête
  d'enregistrement pendant la dictée ; dictée locale désactivée sur le banc : bouton inactif, focusable, info-bulle explicite ;
- 375 × 812 : une colonne, aucun défilement horizontal.
Aucune erreur JavaScript ni CSP, aucune requête vers une autre adresse que le banc. Non inscrit dans ``PARCOURS`` : l'intégrateur l'inscrit.
Lancement seul : ``python scripts/parcours_p108q.py [--sortie DOSSIER]``.
"""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
NAV = None
SANS_DEFILEMENT = 'document.documentElement.scrollWidth <= window.innerWidth + 1'
# Micro simulé : reconnaissance « Rapide » du navigateur remplacée par une fausse, qui rend la phrase préparée par le parcours (aucun son,
# aucun réseau). Avertissement du mode Rapide déjà accepté, comme après le premier usage.
MICRO_SIMULE = """(() => {
  try { localStorage.setItem('axiorhub-dictation-mode-v310', sessionStorage.getItem('q108-local') === '1' ? 'local' : 'browser');
        localStorage.setItem('axiorhub-browser-speech-warning-v310', 'accepted'); } catch (e) {}
  window.__dictees = [];
  class Fausse {
    start() { this.t = setTimeout(() => this.fin(), 300); }
    fin() { clearTimeout(this.t); const texte = window.__dictees.shift() || '';
      if (texte && this.onresult) { const r = [{transcript: texte}]; r.isFinal = true; this.onresult({resultIndex: 0, results: [r]}); }
      setTimeout(() => this.onend && this.onend(), 30); }
    stop() { this.fin(); }
    abort() { clearTimeout(this.t); }
  }
  window.SpeechRecognition = Fausse; window.webkitSpeechRecognition = Fausse;
})();"""


def _nav():
    if NAV is not None:
        return NAV
    import __main__ as nav
    return nav


def verifier(condition, message):
    _nav().verifier(condition, message)


def _champ(p, nom):
    return p.locator('[data-q108-champ="%s"]' % nom)


def _ouvrir_groupe(p, groupe):
    d = p.locator('details.q108-groupe[data-groupe="%s"]' % groupe)
    if not d.evaluate('n => n.open'):
        d.locator('summary').click()


def _dictee(banc, s):
    """« Dicter la réponse » avec le micro simulé ; rien n'est envoyé au serveur avant « Enregistrer et continuer »."""
    p = s.page
    # sur cette page seulement : le téléchargement du brouillon laisse, en Chromium sans fenêtre, une cible vide dans le contexte, qui
    # ne répond jamais et bloquerait un script d'initialisation posé sur tout le contexte
    p.add_init_script(MICRO_SIMULE)
    p.reload(wait_until='load')
    p.wait_for_load_state('networkidle')
    _ouvrir_groupe(p, 'operation')
    envois = []
    p.on('request', lambda r: envois.append(r.url) if 'questionnaire/' in r.url and r.method == 'POST' else None)
    mic = _champ(p, 'tribunal').locator('.ax108-dictee')
    verifier(mic.count() == 1 and mic.get_attribute('aria-label') == 'Dicter la réponse' and mic.get_attribute('aria-disabled') == 'false',
             'bouton « Dicter la réponse » absent, sans nom ou inactif')
    champ = _champ(p, 'tribunal').locator('input')
    champ.fill('Tribunal de commerce de')
    p.evaluate("() => { window.__dictees.push('Valbrune'); }")
    mic.focus()
    p.keyboard.press('Enter')                                             # clavier : bouton focusable
    p.locator('[data-q108-champ="tribunal"] .ax108-dictee-statut', has_text='relisez').wait_for(timeout=10000)
    verifier(champ.input_value() == 'Tribunal de commerce de Valbrune', 'texte dicté mal inséré : %r' % champ.input_value())
    date = _champ(p, 'date_acte')
    p.evaluate("() => { window.__dictees.push('30 février 2027'); }")
    date.locator('.ax108-dictee').click()
    date.locator('.ax108-dictee-statut', has_text='non valide').wait_for(timeout=10000)
    verifier(date.locator('input').input_value() == '', 'une date non valide a été posée')
    verifier('30 février 2027' in date.locator('.ax108-dictee-statut').inner_text(), 'la valeur reconnue n’est pas affichée pour correction')
    p.evaluate("() => { window.__dictees.push('le 14 janvier 2027'); }")
    date.locator('.ax108-dictee').click()
    date.locator('.ax108-dictee-statut', has_text='relisez').wait_for(timeout=10000)
    verifier(date.locator('input').input_value() == '2027-01-14', 'date dictée valide non posée : %r' % date.locator('input').input_value())
    verifier(not envois, 'requête d’enregistrement pendant la dictée : %s' % envois)
    s.propre('dictée des réponses')
    # dictée locale (mode confidentiel) désactivée sur le banc : bouton inactif, focusable, info-bulle explicite
    p.evaluate("() => sessionStorage.setItem('q108-local', '1')")
    p.reload(wait_until='load')
    p.wait_for_load_state('networkidle')
    _ouvrir_groupe(p, 'parties')
    mic = _champ(p, 'client').locator('.ax108-dictee')
    verifier(mic.get_attribute('aria-disabled') == 'true' and 'désactivée' in (mic.get_attribute('title') or ''), 'micro indisponible non signalé : %s' % mic.get_attribute('title'))
    mic.focus()
    verifier(p.evaluate("() => document.activeElement && document.activeElement.classList.contains('ax108-dictee')"), 'bouton inactif non focusable')
    p.evaluate("() => sessionStorage.removeItem('q108-local')")
    s.propre('micro indisponible')


def p108q_questionnaire(banc, s):
    p = s.page
    sortie = Path(banc.sortie)
    s.connecter()
    s.aller('/corporate?vue=bibliotheque&modele=contrat_prestation')
    carte = p.locator('[data-q108-carte]')
    verifier(carte.count() == 1, 'carte « Questionnaire de l’acte » absente de la fiche du modèle')
    carte.locator('select[name=dossier]').select_option('DOS-001')
    with p.expect_navigation():
        carte.locator('button[type=submit]').click()
    p.wait_for_load_state('networkidle')
    verifier(p.locator('[data-q108]').count() == 1, 'le formulaire de l’acte ne s’est pas ouvert : ' + p.url)
    s.propre('ouverture du questionnaire')
    # formulaire et aperçu côte à côte, un seul groupe déplié
    form, apercu = p.locator('.q108-form').bounding_box(), p.locator('.q108-apercu').bounding_box()
    verifier(form and apercu and apercu['x'] >= form['x'] + form['width'] - 2 and abs(apercu['y'] - form['y']) < 40,
             'aperçu pas à côté du formulaire : %s / %s' % (form, apercu))
    verifier(p.locator('details.q108-groupe[open]').count() == 1, 'plusieurs groupes dépliés')
    verifier('Brouillon partiel' in p.locator('.q108-tete').inner_text(), 'état « Brouillon partiel » non affiché')
    p.screenshot(path=str(sortie / 'P108q-1366x900.png'), full_page=False)
    # saisie non enregistrée, puis rechargement : elle revient
    _ouvrir_groupe(p, 'parties')
    _champ(p, 'client').locator('input').fill('KERVILAC')
    _champ(p, 'prestataire').locator('input').fill('ZORVAL')
    _ouvrir_groupe(p, 'operation')
    _champ(p, 'plafond').locator('button[data-statut=inconnu]').click()
    p.reload(wait_until='load')
    p.wait_for_load_state('networkidle')
    verifier(p.locator('.q108-brouillon').is_visible(), 'avis « saisie restaurée » absent après rechargement')
    verifier(_champ(p, 'client').locator('input').input_value() == 'KERVILAC', 'saisie perdue au rechargement')
    verifier(_champ(p, 'plafond').locator('button[data-statut=inconnu]').get_attribute('aria-pressed') == 'true', '« Je ne sais pas » perdu au rechargement')
    with p.expect_navigation():
        p.click('.q108-form button[type=submit]')
    p.wait_for_load_state('networkidle')
    texte = p.locator('.q108-apercu').inner_text()
    verifier('KERVILAC' in texte and 'ZORVAL' in texte, 'les parties ne sont pas dans l’aperçu')
    verifier(p.locator('.q108-section[data-section="art-confidentialite"][data-etat="redigee"]').count() == 1, 'section indépendante non rédigée')
    verifier(p.locator('.q108-section[data-section="art-responsabilite"][data-etat="a_completer"]').count() == 1, '« Je ne sais pas » ne laisse pas « À compléter »')
    verifier(p.locator('[data-q108]').get_attribute('data-revision') == '2', 'révision non incrémentée')
    s.propre('enregistrer et continuer')
    # second onglet resté sur la révision 2 ; le premier enregistre d'abord
    autre = s.context.new_page()
    autre.goto(p.url, wait_until='load')
    autre.wait_for_load_state('networkidle')
    _ouvrir_groupe(p, 'operation')
    _champ(p, 'prestations').locator('input, textarea').first.fill('Maintenance fictive du site')
    with p.expect_navigation():
        p.click('.q108-form button[type=submit]')
    p.wait_for_load_state('networkidle')
    _ouvrir_groupe(autre, 'operation')
    _champ(autre, 'prestations').locator('input, textarea').first.fill('Autre description fictive')
    with autre.expect_response(lambda r: 'questionnaire/repondre' in r.url) as attendu:
        autre.click('.q108-form button[type=submit]')
    verifier(attendu.value.status == 409, 'enregistrement d’une révision périmée non refusé : %s' % attendu.value.status)
    autre.locator('.q108-etat', has_text='modifié entre-temps').wait_for(timeout=10000)   # attente sans évaluation de texte (CSP sans unsafe-eval)
    autre.reload(wait_until='load')
    verifier(_champ(autre, 'prestations').locator('input, textarea').first.input_value() == 'Autre description fictive', 'saisie du second onglet perdue après le 409')
    autre.close()
    s.erreurs.clear()                                   # refus 409 voulu, signalé par la console
    # brouillon Word partiel
    with p.expect_download() as tele:
        p.click('.q108-telecharger')
    verifier('brouillon partiel' in tele.value.suggested_filename, 'nom du brouillon : %s' % tele.value.suggested_filename)
    # correction manuelle d'une section
    section = p.locator('.q108-section[data-section="art-confidentialite"]')
    section.locator('details.q108-corriger summary').click()
    section.locator('textarea').fill('Chaque partie garde le secret pendant la durée fixée par l’avocat (correction fictive).')
    with p.expect_navigation():
        section.locator('.q108-enregistrer-correction').click()
    p.wait_for_load_state('networkidle')
    verifier(p.locator('.q108-section[data-section="art-confidentialite"][data-etat="corrigee"]').count() == 1, 'correction manuelle non conservée')
    s.propre('brouillon et correction')
    # IA en pause : la demande l'annonce, le formulaire reste utilisable
    from agent import arret5643
    with banc.desk() as d:
        arret5643.pause_all(d, True, 'banc')
    p.click('.q108-rediger')
    p.locator('.q108-etat', has_text='IA en pause').wait_for(timeout=10000)
    with banc.desk() as d:
        arret5643.pause_all(d, False, 'banc')
    s.propre('IA en pause')
    _dictee(banc, s)
    # téléphone : une colonne, aucun défilement horizontal
    p.set_viewport_size({'width': 375, 'height': 812})
    p.reload(wait_until='load')
    form, apercu = p.locator('.q108-form').bounding_box(), p.locator('.q108-apercu').bounding_box()
    verifier(apercu['y'] >= form['y'] + form['height'] - 2, 'aperçu non placé sous le formulaire à 375 px')
    verifier(p.evaluate(SANS_DEFILEMENT), 'défilement horizontal à 375 px')
    p.screenshot(path=str(sortie / 'P108q-375x812.png'), full_page=False)
    s.propre('questionnaire au téléphone')


PARCOURS = (('P108q-questionnaire', 'Questionnaire de l’acte : ouverture, saisie gardée, 409, brouillon partiel, correction, IA en pause, mobile', p108q_questionnaire),)


def main(argv=None):
    """Lancement seul (sans inscription dans ``PARCOURS`` du banc)."""
    global NAV
    import argparse
    import importlib.util
    import tempfile
    parser = argparse.ArgumentParser()
    parser.add_argument('--sortie', default=str(Path(tempfile.gettempdir()) / 'axiorhub-parcours-p108q'))
    parser.add_argument('--navigateur', default='chromium')
    parser.add_argument('--parcours', default=','.join(i for i, _, _ in PARCOURS))
    args = parser.parse_args(argv)
    sys.path.insert(0, str(ROOT))
    sys.path.insert(0, str(ROOT / 'scripts'))
    spec = importlib.util.spec_from_file_location('parcours_navigateur', str(ROOT / 'scripts' / 'parcours-navigateur.py'))
    NAV = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(NAV)
    connus = {i for i, _, _ in NAV.PARCOURS}
    NAV.PARCOURS = NAV.PARCOURS + tuple(x for x in PARCOURS if x[0] not in connus)   # déjà inscrit par l'intégrateur : une seule fois
    return NAV.lancer(args.navigateur, args.sortie, tuple(x for x in args.parcours.split(',') if x), continuer=True)


if __name__ == '__main__':
    sys.exit(main())
