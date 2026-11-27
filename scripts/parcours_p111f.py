"""5.6.111 (lot F) : parcours P111f du banc ``scripts/parcours-navigateur.py`` — onglet « Conversation IA » du dossier, dans un vrai navigateur
(Edge sous Windows), version serveur du banc (sans poste : pas de navigateur supervisé), données fictives, faux modèle du banc, aucun réseau.

P111f-conversation :
- 1536 × 864 (1920 × 1080 à 125 %) puis 1920 × 1080 : onglet ouvert depuis la fiche du dossier ; saisie, micro, Envoyer, « Pause de l'IA sur
  ce dossier », Document / Navigateur et Agrandir visibles et non recouverts sans défiler ;
- dictée commune attachée au micro (``window.axiorhubDictee.attacher`` : attributs posés par le composant commun), sans enregistrement ;
- contexte : « Modifier » ouvre la liste des fichiers du dossier ; « Ouvrir » affiche le document dans le volet d'aperçu commun à droite ;
  ses commandes (Télécharger, Retour à la liste) visibles sans défiler ;
- instruction envoyée (double clic sur Envoyer) : un seul message et un seul travail en file ; reçu affiché, saisie vidée APRÈS le reçu ;
  le banc exécute la file (faux modèle) : la réponse s'affiche dans le fil, l'état passe à « Terminée » ;
- Navigateur : message honnête « non disponible sur ce serveur » (aucune image, aucun cadre présenté comme un navigateur) ;
- retour au même contexte : brouillon tapé, page quittée puis retour arrière : même discussion, même document ouvert, même brouillon ;
- 375 × 812 : aucun défilement horizontal ; bascule Discussion / Aperçu ;
- thème sombre : capture ; aucune erreur JavaScript ni CSP, aucune requête vers une autre adresse que le banc (contrôles du banc).
Non inscrit dans ``PARCOURS`` : l'intégrateur l'inscrit. Lancement seul :
``python scripts/parcours_p111f.py [--navigateur msedge] [--sortie DOSSIER]``.
"""
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parent.parent
NAV = None
SANS_DEFILEMENT = 'document.documentElement.scrollWidth <= window.innerWidth + 1'
VISIBLE = """(sel) => { const n = Array.from(document.querySelectorAll(sel)).find(x => x.getClientRects().length && x.getBoundingClientRect().width > 0);
  if (!n) return 'absent'; const r = n.getBoundingClientRect(), vv = window.visualViewport || {height: innerHeight, offsetTop: 0, width: innerWidth};
  if (r.top < vv.offsetTop - 1 || r.bottom > vv.offsetTop + vv.height + 1 || r.left < -1 || r.right > vv.width + 1)
    return 'hors de la fenêtre (' + Math.round(r.top) + '-' + Math.round(r.bottom) + ' / ' + Math.round(vv.height) + ')';
  const e = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
  return e && (e === n || n.contains(e) || e.contains(n)) ? '' : 'recouvert par ' + (e ? e.tagName + '.' + e.className : 'rien'); }"""
COMMANDES = ('#c111-saisie', '[data-c111-envoyer]', '[data-c111-micro]', '.c111-suivi [data-d27-pause]', '[data-c111-mode="document"]',
             '[data-c111-mode="navigateur"]', '[data-c111-agrandir]', '[data-c111-zone="historique"]')


def _nav():
    if NAV is not None:
        return NAV
    import __main__ as nav
    return nav


def verifier(condition, message):
    _nav().verifier(condition, message)


def _visible(p, sel, lieu):
    etat = p.evaluate(VISIBLE, sel)
    verifier(not etat, '%s : %s %s' % (lieu, sel, etat))


def _attendre(p, expression, quoi, secondes=10.0):
    """Attente par ``evaluate`` (la CSP d'AxiorHub refuse l'évaluation de chaînes qu'utilise ``wait_for_function``)."""
    fin = time.time() + secondes
    while time.time() < fin:
        if p.evaluate(expression):
            return
        p.wait_for_timeout(200)
    verifier(False, 'attente dépassée : ' + quoi)


def semer(banc):
    """Deux fichiers fictifs dans le dossier DOS-001 (stockage local du banc) et leur fiche au registre du dossier (aucune lecture par l'IA)."""
    nav = _nav()
    dossier = next(p for p in (banc.cloud / 'Dossiers').iterdir() if p.name.startswith('DOS-001'))
    chemin_docx = '/Dossiers/%s/Conclusions adverses MARTIN.docx' % dossier.name
    (dossier / 'Conclusions adverses MARTIN.docx').write_bytes(nav.docx_fictif('Conclusions fictives de MARTIN.', 'Demande de paiement de mille euros.',
                                                                              'Par ces motifs, plaise au tribunal.'))
    chemin_txt = '/Dossiers/%s/Note interne DURAND.txt' % dossier.name
    (dossier / 'Note interne DURAND.txt').write_text('Note fictive : rappeler le client DURAND.', encoding='utf-8')
    with banc.desk() as d:
        from agent import modele5651
        modele5651.ensure_schema(d)
        for chemin, nom, lecture in ((chemin_docx, 'Conclusions adverses MARTIN.docx', 'lisible'), (chemin_txt, 'Note interne DURAND.txt', 'inconnu')):
            d.db.execute('INSERT OR REPLACE INTO d51_documents(matter,path,name,nature,reading,modified,updated) VALUES(?,?,?,?,?,?,?)',
                         ('DOS-001', chemin, nom, 'conclusions' if nom.endswith('.docx') else 'a_classer', lecture, d.now(), d.now()))
        d.db.commit()
    return chemin_docx


def reponse_chat(corps):
    """Faux modèle du banc, pour ce parcours seulement : réponse au format de l'assistant (schéma ``CHAT`` demandé par ``format``) ; sinon la
    réponse ordinaire du banc. Aucune pertinence juridique n'est vérifiée ici."""
    import json
    schema = corps.get('format') if isinstance(corps.get('format'), dict) else {}
    if 'answer' in (schema.get('properties') or {}):
        return json.dumps({'answer': 'Réponse fictive du banc : trois points à relire.', 'source_ids': [], 'limits': ['Banc fictif'], 'proposed_actions': []},
                          ensure_ascii=False)
    return _nav().reponse_fictive(corps)


def _nb(banc, sql):
    with banc.desk() as d:
        return d.db.execute(sql).fetchone()[0]


def p111f_conversation(banc, s):
    p = s.page
    sortie = Path(banc.sortie)
    banc.modele.reponse = reponse_chat
    p.set_viewport_size({'width': 1536, 'height': 864})
    s.connecter()
    s.aller('/dossier?id=DOS-001')                       # première ouverture : reconstruction de la fiche du dossier (sans IA)
    chemin = semer(banc)
    p.click('nav.d79-tabs a[href*="vue=conversation"]')
    p.wait_for_selector('#c111')
    p.wait_for_load_state('networkidle')
    verifier('Conversation IA' in p.inner_text('nav.d79-tabs a[aria-current="page"]'), 'onglet « Conversation IA » non marqué comme actif')
    for nom, vue in (('1920 × 1080', {'width': 1920, 'height': 1080}), ('125 %', {'width': 1536, 'height': 864})):
        p.set_viewport_size(vue)
        s.aller('/dossier?id=DOS-001&vue=conversation')   # onglet ouvert à cette taille : aucun défilement fait par la personne
        p.wait_for_timeout(400)
        for sel in COMMANDES:
            _visible(p, sel, 'onglet ouvert (%s)' % nom)
        p.screenshot(path=str(Path(banc.sortie) / ('P111f-onglet-%s.png' % nom.replace(' ', '').replace('%', 'pc').replace('×', 'x'))))
    # dictée commune attachée (composant de v56106-redaction.js), aucun enregistrement lancé
    verifier(p.get_attribute('#c111', 'data-c111-dictee') == 'attachee', 'dictée non attachée au micro')
    verifier(p.get_attribute('[data-c111-micro]', 'aria-pressed') == 'false' and p.get_attribute('[data-c111-micro]', 'data-titre'),
             'le micro ne porte pas les attributs posés par window.axiorhubDictee.attacher')
    s.propre('onglet ouvert')

    # contexte : liste des fichiers, ouverture dans le volet d'aperçu commun
    p.click('[data-c111-modifier]')
    p.wait_for_selector('#c111-choix:not([hidden]) [data-c111-fichier]')
    verifier(p.locator('[data-c111-fichier]').count() == 2, 'liste des fichiers du dossier incomplète')
    verifier('pas encore lus' in p.inner_text('#c111-choix'), 'fichier non lu non signalé')
    p.click('#c111-choix a.c111-ouvrir[data-d79-doc$=".docx"]')
    p.wait_for_selector('#d79-preview:not([hidden]) .d79-docview, #d79-preview:not([hidden]) .d79-preview-body p', timeout=15000)
    verifier(p.is_hidden('[data-c111-vide]'), 'texte « Aucun document ouvert » encore affiché avec un document ouvert')
    _attendre(p, "!!document.querySelector('#d79-preview .d79-docview, #d79-preview iframe')", 'aperçu du document dans le volet de droite')
    cadre = p.locator('#d79-preview iframe')
    texte_doc = cadre.first.content_frame.locator('body').inner_text() if cadre.count() else p.inner_text('#d79-preview')   # aperçu HTML isolé ou article
    verifier('Conclusions fictives de MARTIN' in texte_doc, 'aperçu du document absent du volet de droite')
    p.click('[data-c111-modifier]')
    for sel in ('#d79-preview [data-d79-dl]', '#d79-preview [data-d79-close]', '#c111-saisie', '[data-c111-envoyer]'):
        _visible(p, sel, 'document ouvert à droite (125 %)')
    p.screenshot(path=str(sortie / 'P111f-document-125pc.png'))
    s.propre('document ouvert')

    # instruction : double clic sur Envoyer → un seul message, un seul travail ; reçu puis saisie vidée
    travaux = _nb(banc, "SELECT COUNT(*) FROM jobs WHERE kind='assistant_answer'")
    p.fill('#c111-saisie', 'Résume les conclusions adverses de MARTIN en trois points.')
    p.dblclick('[data-c111-envoyer]')
    _attendre(p, "document.querySelector('[data-c111-recu]').textContent.indexOf('Message n°') >= 0", 'reçu de la demande')
    verifier(p.input_value('#c111-saisie') == '', 'saisie non vidée après le reçu')
    p.wait_for_timeout(800)
    verifier(_nb(banc, "SELECT COUNT(*) FROM jobs WHERE kind='assistant_answer'") == travaux + 1, 'double clic : plus d’un travail en file')
    verifier(_nb(banc, "SELECT COUNT(*) FROM assistant_messages WHERE role='user' AND content LIKE 'Résume les conclusions adverses de MARTIN%'") == 1,
             'double clic : plus d’un message enregistré')
    p.wait_for_selector('.c111-msg-vous')
    pris = banc.travailler()
    verifier(pris >= 1, 'aucun travail exécuté par le banc')
    with banc.desk() as d:
        fin = d.db.execute("SELECT status, result FROM jobs WHERE kind='assistant_answer' ORDER BY id DESC LIMIT 1").fetchone()
    verifier(fin['status'] == 'done', 'travail de la réponse en échec : %s %s' % (fin['status'], str(fin['result'])[:400]))
    p.wait_for_selector('.c111-msg-ia', timeout=20000)
    verifier('Réponse fictive du banc' in p.inner_text('[data-c111-messages]'), 'réponse du faux modèle absente du fil')
    _attendre(p, "document.querySelector('[data-c111-suivi]').textContent.indexOf('Terminée') >= 0", 'état « Terminée » de la demande', 15)
    verifier(p.is_hidden('[data-c111-arreter]'), '« Arrêter » proposé pour une demande terminée')
    p.screenshot(path=str(sortie / 'P111f-reponse-125pc.png'))
    s.propre('instruction et réponse')

    # Navigateur : aucune vue simulée sur le serveur du banc
    p.click('[data-c111-mode="navigateur"]')
    texte = p.inner_text('[data-c111-volet="navigateur"]')
    verifier('non disponible sur ce serveur' in texte, 'message honnête du navigateur absent')
    verifier(p.locator('[data-c111-volet="navigateur"] iframe, [data-c111-volet="navigateur"] img, [data-c111-volet="navigateur"] canvas').count() == 0,
             'image ou cadre présenté comme un navigateur')
    p.click('[data-c111-mode="document"]')

    # retour au même contexte : discussion, document et brouillon
    titre = p.inner_text('[data-c111-fil-titre]')
    brouillon = 'Brouillon fictif : compléter la réponse pour DURAND.'
    p.fill('#c111-saisie', brouillon)
    p.wait_for_timeout(1600)                              # enregistrement différé du brouillon (0,9 s)
    s.aller('/aujourdhui')
    p.go_back()
    p.wait_for_selector('#c111')
    _attendre(p, "document.getElementById('c111-saisie').value.indexOf('Brouillon fictif') === 0", 'brouillon rendu au retour')
    _attendre(p, "!!document.querySelector('#d79-preview:not([hidden])')", 'document rouvert au retour')
    _attendre(p, "document.querySelector('[data-c111-fil-titre]').textContent === %r" % titre, 'même discussion rouverte au retour (%s)' % titre)
    verifier(p.locator('.c111-msg-ia').count() >= 1, 'fil non rouvert au retour')
    s.propre('retour au même contexte')

    # mode agrandi, petit écran, thème sombre
    p.click('[data-c111-agrandir]')
    verifier(p.is_hidden('.c111-chat'), 'mode agrandi : la discussion reste affichée')
    p.click('[data-c111-agrandir]')
    p.set_viewport_size({'width': 375, 'height': 812})
    s.aller('/dossier?id=DOS-001&vue=conversation')   # ouverture sur un téléphone
    p.wait_for_timeout(400)
    verifier(p.evaluate(SANS_DEFILEMENT), 'défilement horizontal de l’onglet à 375 px')
    _visible(p, '[data-c111-vue="discussion"]', 'petit écran')
    p.click('[data-c111-vue="discussion"]')
    verifier(p.is_hidden('.c111-droite') and p.is_visible('#c111-saisie'), 'petit écran : bascule Discussion inopérante')
    p.click('[data-c111-vue="apercu"]')
    verifier(p.is_visible('.c111-droite') and p.is_hidden('.c111-chat'), 'petit écran : bascule Aperçu inopérante')
    verifier(p.evaluate(SANS_DEFILEMENT), 'défilement horizontal en mode Aperçu à 375 px')
    _visible(p, '#d79-preview [data-d79-close]', 'petit écran, aperçu (Retour à la liste)')
    p.screenshot(path=str(sortie / 'P111f-375px.png'))
    p.set_viewport_size({'width': 1536, 'height': 864})
    p.evaluate("() => { document.documentElement.dataset.theme = 'dark'; }")
    p.wait_for_timeout(200)
    p.screenshot(path=str(sortie / 'P111f-sombre-125pc.png'))
    s.propre('agrandi, petit écran, thème sombre')


PARCOURS = (('P111f-conversation', 'Conversation IA du dossier : commandes visibles à 125 %, dictée attachée, document à droite, instruction sans '
             'doublon, réponse, navigateur honnête, retour au même contexte, 375 px', p111f_conversation),)


def main(argv=None):
    """Lancement seul (sans inscription dans ``PARCOURS`` du banc)."""
    global NAV
    import argparse
    import importlib.util
    import tempfile
    parser = argparse.ArgumentParser()
    parser.add_argument('--sortie', default=str(Path(tempfile.gettempdir()) / 'axiorhub-parcours-p111f'))
    parser.add_argument('--navigateur', default='msedge')
    parser.add_argument('--parcours', default=','.join(i for i, _, _ in PARCOURS))
    args = parser.parse_args(argv)
    sys.path.insert(0, str(ROOT))
    sys.path.insert(0, str(ROOT / 'scripts'))
    spec = importlib.util.spec_from_file_location('parcours_navigateur', str(ROOT / 'scripts' / 'parcours-navigateur.py'))
    NAV = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(NAV)
    if not any(ident == PARCOURS[0][0] for ident, _, _ in NAV.PARCOURS):   # déjà inscrits par le banc depuis l'intégration
        NAV.PARCOURS = NAV.PARCOURS + PARCOURS
    return NAV.lancer(args.navigateur, args.sortie, tuple(x for x in args.parcours.split(',') if x), continuer=True)


if __name__ == '__main__':
    sys.exit(main())
