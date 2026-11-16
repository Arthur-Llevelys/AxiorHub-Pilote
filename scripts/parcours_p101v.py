"""5.6.101 : parcours P101v du banc ``scripts/parcours-navigateur.py`` (vrai navigateur, données fictives, aucun réseau, aucun modèle).

- P101v (lot V) Aujourd'hui › volet de relecture : à 1920 × 1080, un courriel à relire, une note de l'IA, une décision de la file, un document
  Word et un PDF, une échéance à vérifier s'ouvrent dans le volet ancré à droite ; la liste reste visible et cliquable (une autre ligne remplace
  le contenu, la ligne ouverte porte aria-current) ; « Suivant » passe à la ligne suivante ; le corps du courriel (cadre HTML isolé) et le
  document (cadre de l'éditeur, faux ``api.js`` servi par le banc) sont visibles sans clic de plus ; bascule HTML · Texte brut ; Valider un
  courriel, Modifier un document (édition dans le cadre, lien « Ouvrir dans l'éditeur »), Instruction complémentaire sur un document, Écarter
  une décision de la file, chacun par la route existante ; repli sur l'aperçu simplifié quand le serveur de documents ne répond pas ; barre
  des quatre actions visible sans défiler à 1920 × 1080, 1366 × 768, 1280 × 720, 125 % et 200 % ; 390 px : fenêtre flottante, sans défilement
  horizontal ; Détacher / Ancrer ; Échap ferme et rend le focus à la ligne. Captures : volet ancré (1920) et fenêtre flottante (390).

Le vrai serveur de documents (ONLYOFFICE / Euro-Office) n'est jamais joint : ``https://office.example.test`` est intercepté par le banc.
Enregistrement : ``parcours-navigateur.py`` (après P101r) et ``test_v5694_parcours``.
"""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import secrets
import time

SANS_DEFILEMENT = 'document.documentElement.scrollWidth <= window.innerWidth + 1'
OFFICE = 'https://office.example.test'
FAUX_API = r"""window.DocsAPI = { DocEditor: function (id, cfg) {
  var hote = document.getElementById(id), d = document.createElement('div');
  d.id = 'faux-editeur'; d.setAttribute('data-mode', cfg.editorConfig.mode);
  d.textContent = 'Éditeur simulé (' + cfg.editorConfig.mode + ') : ' + cfg.document.title;
  hote.appendChild(d); this.destroyEditor = function () { d.remove(); };
  setTimeout(function () { cfg.events.onAppReady(); }, 50);
} };"""
BARRE_VISIBLE = """() => { const b = document.querySelector('dialog.f96-fenetre[open] .v101-pied'); if (!b) return 'absente';
  const r = b.getBoundingClientRect(); if (!(r.top >= 0 && r.bottom <= window.innerHeight + 1 && r.height > 0)) return 'hors vue ' + JSON.stringify([r.top, r.bottom, window.innerHeight]);
  for (const x of b.querySelectorAll('[data-v101-action]')) { const q = x.getBoundingClientRect(); if (q.width < 1) continue;
    const dessus = document.elementFromPoint(q.left + q.width / 2, q.top + q.height / 2); if (!dessus || !x.contains(dessus)) return 'bouton couvert : ' + x.textContent + ' par ' + (dessus ? (dessus.id || dessus.className || dessus.tagName) : 'rien'); }
  return 'ok'; }"""

CHEVAUCHEMENT = """() => { const v = document.querySelector('dialog.f96-fenetre[open]'); if (!v) return 'absent';
  if (!v.classList.contains('v101-ancre')) return 'ok';
  const nom = x => x ? (x.id || (typeof x.className === 'string' ? x.className : '') || x.tagName) : 'rien';
  const rv = v.getBoundingClientRect();
  const pts = []; for (let i = 1; i < 12; i++) for (let k = 1; k < 8; k++) pts.push([k / 8, i / 12]);
  for (const dx of [16, 38]) for (const dy of [16, 36]) for (const [cx, cy] of [[0, 0], [1, 0], [0, 1], [1, 1]])   // coins (pastilles flottantes)
    pts.push([cx ? 1 - dx / rv.width : dx / rv.width, cy ? 1 - dy / rv.height : dy / rv.height]);
  for (const [fx, fy] of pts) { const x = rv.left + rv.width * fx, y = rv.top + rv.height * fy;
    const d = document.elementFromPoint(x, y); if (!d || !v.contains(d)) return 'volet couvert en ' + Math.round(x) + ',' + Math.round(y) + ' par ' + nom(d); }
  for (const a of document.querySelectorAll('#f100-file a[data-f96-action]')) { const r = a.getBoundingClientRect();
    if (r.width < 1 || r.bottom < 0 || r.top > innerHeight) continue;
    for (const fx of [0.1, 0.5, 0.9]) { const px = r.left + r.width * fx, py = r.top + r.height / 2;
      if (px < 0 || py < 0 || px >= innerWidth || py >= innerHeight) continue;   // 5.6.103 : point hors de la fenêtre (lien à moitié visible)
      const d = document.elementFromPoint(px, py);
      if (!d || !a.contains(d)) return 'lien de la liste couvert (' + a.textContent.trim() + ') par ' + nom(d); } }
  return 'ok'; }"""


def _nav():
    import __main__ as nav
    return nav


def semer_pdf(banc):
    """Un avis fictif au format PDF, inscrit au registre comme un document préparé (forme du journal d'une demande de document)."""
    from agent import docrequest520, livrables5689
    nav = _nav()
    m = nav.MATTERS[0]
    chemin = '/Dossiers/%s - %s c. %s/PROJETS/Avis %s.pdf' % (m[0], m[1], m[2], m[1])
    brut = b'%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj 2 0 obj<</Type/Pages/Kids[]/Count 0>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n'
    fichier = banc.cloud / chemin.lstrip('/')
    fichier.parent.mkdir(parents=True, exist_ok=True)
    fichier.write_bytes(brut)
    sha = hashlib.sha256(brut).hexdigest()
    maintenant = time.strftime('%Y-%m-%dT%H:%M:%S+00:00', time.gmtime())
    with banc.desk() as d:
        docrequest520.ensure_schema(d)
        d.db.execute('INSERT INTO docreq520(id,matter,request,kind,status,path,result,created,updated) VALUES(?,?,?,?,?,?,?,?,?)',
                     ('banc-' + secrets.token_hex(6), m[0], 'Préparer un avis fictif', 'avis', 'cree', chemin,
                      json.dumps({'title': 'Avis ' + m[1], 'sha256': sha, 'readback_sha256': sha}), maintenant, maintenant))
        d.db.commit()
        livrables5689.synchroniser(d, force=True)
        row = d.db.execute('SELECT document_id FROM prepared_items5689 WHERE path=? ORDER BY version_number DESC LIMIT 1', (chemin,)).fetchone()
    return row[0]


def semer_decision(banc, dossier='DOS-001'):
    """Une contradiction de montants (décision de la file « À décider » : Pris en compte / Reporter), comme l'inscrit la mémoire du dossier."""
    from agent import memoire5651
    with banc.desk() as d:
        memoire5651.record_change(d, dossier, 'montant_modifie', 'fait', 'prix-p101v', 'v2', 'Montant du loyer modifié : 1 200 € au lieu de 1 000 €',
                                  {'consequence': 'Le montant retenu dans les écritures change : vérifiez la pièce source avant de l’utiliser.'})


def regler_office(banc, actif=True):
    with banc.desk() as d:
        d.setting('office440:config', {'server_url': OFFICE, 'engine': 'euro-office'} if actif else {})


def ouvrir(p, selecteur):
    lien = p.locator(selecteur).first
    lien.click()
    p.wait_for_selector('dialog.f96-fenetre[open]:not([aria-busy])', timeout=15000)
    p.wait_for_selector('dialog.f96-fenetre[open] .v101-pied:not([hidden])', timeout=15000)
    return lien


def p101v_volet(banc, s):
    from parcours_p100 import semer_propositions
    from parcours_p101r import semer_note
    nav = _nav()
    verifier = nav.verifier
    p = s.page
    sortie = Path(banc.sortie) if getattr(banc, 'sortie', None) else None
    nav._accueil96(banc, meteo=False)                                   # échéance à vérifier, document Word préparé
    with banc.desk() as d:                                              # le document Word est aussi dans « À relire » (comme après le rattrapage)
        from agent import relecture56101 as r1
        r1.rattraper_sources(d)
    semer_propositions(banc, 2, debut=60)
    note_id, rid = semer_note(banc)
    semer_decision(banc)
    pdf_id = semer_pdf(banc)
    regler_office(banc)
    servis = []

    def faux_office(route):
        servis.append(route.request.url)
        if route.request.url.endswith('/web-apps/apps/api/documents/api.js') and not getattr(banc, 'office_coupe', False):
            return route.fulfill(status=200, content_type='text/javascript', body=FAUX_API)
        return route.abort()
    p.route(OFFICE + '/**', faux_office)
    try:
        p.set_viewport_size({'width': 1920, 'height': 1080})
        s.connecter()
        p.evaluate("() => { try { localStorage.removeItem('ax101:volet'); localStorage.removeItem('ax101:format'); } catch (e) {} }")
        s.aller('/aujourdhui?vue=file')
        natures = p.evaluate("() => Array.from(document.querySelectorAll('#f100-file a[data-f96-action]')).map(a => a.getAttribute('data-f96-action'))")
        for n in ('reponse', 'note_ia', 'file', 'document', 'echeance'):
            verifier(n in natures, 'aucune ligne « %s » ouvrable dans la vue de la file : %r' % (n, natures))
        sans_js = p.evaluate("() => Array.from(document.querySelectorAll('#f100-file a[data-f96-action]')).every(a => a.getAttribute('href') && !a.getAttribute('href').startsWith('#'))")
        verifier(sans_js, 'une ligne ouvrable a perdu son adresse (sans script, la page complète doit rester accessible)')

        # 1. courriel : volet ancré à droite, corps HTML visible d'emblée, barre visible, liste cliquable
        courriel = ouvrir(p, '#f100-file a[data-f96-action="reponse"]')
        boite = p.locator('dialog.f96-fenetre[open]').bounding_box()
        verifier(p.locator('dialog.f96-fenetre.v101-ancre[open]').count() == 1, 'le volet n’est pas ancré à 1920 px')
        verifier(boite['x'] > 1920 * 0.45 and abs(boite['x'] + boite['width'] - 1920) < 2, 'volet pas à droite : %r' % boite)
        verifier(0.3 * 1920 <= boite['width'] <= 0.55 * 1920, 'largeur du volet inattendue : %r' % boite['width'])
        verifier(courriel.get_attribute('aria-current') == 'true', 'la ligne ouverte ne porte pas aria-current')
        cadre = p.frame_locator('dialog.f96-fenetre[open] iframe[data-v101-cadre="envoi"]')
        cadre.locator('p').first.wait_for(timeout=10000)
        verifier('Projet de réponse fictif' in cadre.locator('body').inner_text(), 'corps du courriel absent du cadre HTML')
        verifier(p.locator('dialog.f96-fenetre[open] iframe[data-v101-cadre="envoi"]').get_attribute('sandbox') == '', 'cadre du courriel sans sandbox')
        verifier(p.evaluate(BARRE_VISIBLE) == 'ok', 'barre des actions hors de la vue (1920 × 1080) : %s' % p.evaluate(BARRE_VISIBLE))
        verifier(p.evaluate(CHEVAUCHEMENT) == 'ok', 'chevauchement (1920 × 1080) : %s' % p.evaluate(CHEVAUCHEMENT))
        libelles = p.locator('dialog.f96-fenetre[open] .v101-pied [data-v101-action]').all_inner_texts()
        verifier(libelles == ['Valider', 'Modifier', 'Instruction', 'Écarter'], 'barre inattendue : %r' % libelles)
        # bascule HTML · Texte brut (mémorisée par navigateur)
        p.locator('dialog.f96-fenetre[open] [data-v101-format="texte"]').click()
        p.wait_for_function("() => (document.querySelector('dialog.f96-fenetre[open] iframe[data-v101-cadre=\"envoi\"]') || {}).src.indexOf('format=texte') > 0")
        cadre.locator('pre').wait_for(timeout=10000)
        verifier(cadre.locator('p').count() == 0 and 'Projet de réponse fictif' in cadre.locator('pre').inner_text(), 'texte brut inattendu')
        verifier(p.evaluate(BARRE_VISIBLE) == 'ok', 'barre hors de la vue après la bascule en texte brut')
        p.locator('dialog.f96-fenetre[open] [data-v101-format="html"]').click()
        if sortie:
            s.capture(sortie / 'P101v-volet-1920.png')
        s.propre('volet : courriel')

        # 2. la liste reste cliquable : une autre ligne (note de l'IA) remplace le contenu
        p.locator('#f100-file a[data-f96-action="note_ia"]').first.click()
        p.wait_for_function("() => /prix contesté/.test((document.querySelector('dialog.f96-fenetre[open] .f96-fenetre-contenu') || {}).textContent || '')", timeout=10000)
        verifier(courriel.get_attribute('aria-current') is None, 'l’ancienne ligne garde aria-current')
        verifier(p.locator('dialog.f96-fenetre[open]').count() == 1, 'plusieurs volets ouverts')
        # Suivant : ligne suivante de la liste visible
        avant = p.locator('dialog.f96-fenetre[open] .f96-fenetre-titre').inner_text()
        p.locator('dialog.f96-fenetre[open] .v101-suivant').click()
        p.wait_for_function("t => (document.querySelector('dialog.f96-fenetre[open] .f96-fenetre-titre') || {}).textContent !== t && !document.querySelector('dialog.f96-fenetre[aria-busy]')",
                            arg=avant, timeout=10000)
        s.propre('volet : liste cliquable et Suivant')

        # 3. document Word : cadre de l'éditeur en lecture, puis Modifier (édition dans le cadre, lien vers l'éditeur), Instruction complémentaire
        ouvrir(p, '#f100-file a[data-f96-action="document"]:not([data-f96-cle="%s"])' % pdf_id)
        editeur = p.frame_locator('dialog.f96-fenetre[open] iframe.v101-cadre-office')
        editeur.locator('#faux-editeur[data-mode="view"]').wait_for(timeout=15000)
        p.wait_for_selector('dialog.f96-fenetre[open] [data-v101-office][data-etat="pret"]', timeout=10000)
        verifier(p.evaluate(BARRE_VISIBLE) == 'ok', 'barre hors de la vue avec le cadre de l’éditeur')
        p.locator('dialog.f96-fenetre[open] .v101-pied [data-v101-action="modifier"]').click()
        editeur.locator('#faux-editeur[data-mode="edit"]').wait_for(timeout=15000)
        verifier(p.locator('dialog.f96-fenetre[open] .v101-panneau a', has_text='Ouvrir dans l’éditeur').count() == 1, 'lien « Ouvrir dans l’éditeur » absent en édition')
        p.locator('dialog.f96-fenetre[open] .v101-panneau button', has_text='Terminer l’édition').click()
        p.wait_for_selector('dialog.f96-fenetre[open] [data-v101-office="view"]', timeout=15000)
        p.locator('dialog.f96-fenetre[open] .v101-pied [data-v101-action="instruction"]').click()
        p.locator('dialog.f96-fenetre[open] .v101-panneau textarea[name="consigne"]').fill('Porter le prix à deux mille euros (consigne fictive).')
        with p.expect_response(lambda r: '/corriger' in r.url and r.request.method == 'POST') as rep:
            p.locator('dialog.f96-fenetre[open] .v101-panneau button[type=submit]').click()
        verifier(rep.value.ok, 'instruction complémentaire refusée : %s' % rep.value.status)
        p.wait_for_function("() => /Reprise demandée/.test((document.querySelector('dialog.f96-fenetre[open] .v101-statut') || {}).textContent || '')", timeout=10000)
        s.propre('volet : document Word')

        # 4. PDF : lecture dans le cadre, Modifier dit pourquoi
        verifier(p.locator('#f100-file a[data-f96-action="document"][data-f96-cle="%s"]' % pdf_id).count() == 1, 'ligne du PDF absente de la file')
        ouvrir(p, '#f100-file a[data-f96-action="document"][data-f96-cle="%s"]' % pdf_id)
        editeur.locator('#faux-editeur[data-mode="view"]').wait_for(timeout=15000)
        p.locator('dialog.f96-fenetre[open] .v101-pied [data-v101-action="modifier"]').dispatch_event('click')   # bouton aria-disabled : il dit pourquoi
        verifier('PDF' in p.locator('dialog.f96-fenetre[open] .v101-statut').inner_text(), 'Modifier un PDF ne dit pas pourquoi')
        s.propre('volet : PDF')

        # 5. repli : serveur de documents injoignable → aperçu simplifié et lien vers l'éditeur
        banc.office_coupe = True
        p.locator('dialog.f96-fenetre[open] .f96-fenetre-fermer').click()
        ouvrir(p, '#f100-file a[data-f96-action="document"]:not([data-f96-cle="%s"])' % pdf_id)
        p.wait_for_selector('dialog.f96-fenetre[open] [data-v101-repli]', timeout=15000)
        verifier('serveur de documents' in p.locator('dialog.f96-fenetre[open] [data-v101-repli]').inner_text(), 'repli non annoncé')
        banc.office_coupe = False
        s.erreurs[:] = [x for x in s.erreurs if 'api.js' not in x and 'ERR_FAILED' not in x]    # échec de chargement voulu (serveur coupé)
        s.propre('volet : repli')

        # 6. échéance à vérifier : date, règle et extrait de la pièce ; jamais présentée comme confirmée
        ouvrir(p, '#f100-file a[data-f96-action="echeance"][data-f96-cle="banc-ech-verifier"]')
        verifier('à vérifier' in p.locator('dialog.f96-fenetre[open] .f96-fenetre-contenu').inner_text(), 'l’échéance ne dit pas « à vérifier »')
        s.propre('volet : échéance')

        # 7. Valider un courriel (« Retenir », rien n'est envoyé) : l'élément suivant s'ouvre
        lien_courriel = ouvrir(p, '#f100-file a[data-f96-action="reponse"]')
        cle_courriel = lien_courriel.get_attribute('data-f96-cle')
        titre_courriel = p.locator('dialog.f96-fenetre[open] .f96-fenetre-titre').inner_text()
        with p.expect_response(lambda r: '/m56100/relecture/' in r.url and r.request.method == 'POST') as rep:
            p.locator('dialog.f96-fenetre[open] .v101-pied [data-v101-action="valider"]').click()
        verifier(rep.value.ok, 'Valider le courriel refusé : %s' % rep.value.status)
        p.wait_for_function("t => { const d = document.querySelector('dialog.f96-fenetre[open]'); return d && !d.hasAttribute('aria-busy') && d.querySelector('.f96-fenetre-titre').textContent !== t; }",
                            arg=titre_courriel, timeout=15000)                      # l'élément suivant s'est ouvert seul
        verifier(p.locator('#f100-file a[data-f96-cle="%s"]:visible' % cle_courriel).count() == 0, 'la ligne du courriel validé reste affichée')
        s.propre('volet : Valider un courriel')

        # 8. Écarter une décision de la file (confirmation simple, route existante, révision affichée)
        ouvrir(p, '#f100-file a[data-f96-action="file"]')
        verifier('1 200 €' in p.locator('dialog.f96-fenetre[open] .f96-fenetre-contenu').inner_text(), 'la proposition n’est pas affichée d’emblée')
        p.locator('dialog.f96-fenetre[open] .v101-pied [data-v101-action="ecarter"]').click()
        with p.expect_response(lambda r: '/file/decider' in r.url and r.request.method == 'POST') as rep:
            p.locator('dialog.f96-fenetre[open] .v101-panneau button[type=submit]').click()
        verifier(rep.value.ok, 'Écarter la décision refusé : %s' % rep.value.status)
        with banc.desk() as d:
            etat = d.db.execute("SELECT state FROM d51_queue WHERE item_class='contradiction' ORDER BY at DESC LIMIT 1").fetchone()
        verifier(etat and etat[0] == 'refuse', 'décision de la file non enregistrée : %r' % (tuple(etat) if etat else None))
        s.propre('volet : Écarter une décision de la file')

        # 9. Détacher / Ancrer, Échap rend le focus à la ligne
        lien = ouvrir(p, '#f100-file a[data-f96-action="note_ia"]')
        p.locator('dialog.f96-fenetre[open] .v101-ancrage').click()
        p.wait_for_selector('dialog.f96-fenetre[open]:not(.v101-ancre)', timeout=10000)
        verifier(p.evaluate("() => { try { return localStorage.getItem('ax101:volet'); } catch (e) { return 'x'; } }") == 'flottant', 'préférence « Détacher » non gardée')
        p.locator('dialog.f96-fenetre[open] .v101-ancrage').click()
        p.wait_for_selector('dialog.f96-fenetre.v101-ancre[open]', timeout=10000)
        p.keyboard.press('Escape')
        p.wait_for_function("() => !document.querySelector('dialog.f96-fenetre[open]')", timeout=5000)
        try:   # le focus revient après l'événement « close » (asynchrone)
            p.wait_for_function("() => document.activeElement && document.activeElement.getAttribute('data-f96-action') === 'note_ia'", timeout=3000)
        except Exception:   # noqa: BLE001 — le contrôle ci-dessous dit l'échec
            pass
        verifier(p.evaluate("() => (document.activeElement || {}).getAttribute && document.activeElement.getAttribute('data-f96-action')") == 'note_ia',
                 'Échap ne rend pas le focus à la ligne')
        s.propre('volet : Détacher, Ancrer, Échap')

        # 10. barre visible sans défiler aux tailles demandées ; aucune barre de défilement horizontale
        for nom, vue in (('1920x1080', {'width': 1920, 'height': 1080}), ('1366x768', {'width': 1366, 'height': 768}), ('1280x720', {'width': 1280, 'height': 720}),
                         ('125 %', {'width': 1536, 'height': 864}), ('200 %', {'width': 960, 'height': 540})):
            p.set_viewport_size(vue)
            s.aller('/aujourdhui?vue=file')
            ouvrir(p, '#f100-file a[data-f96-action="note_ia"]')
            verifier(p.evaluate(BARRE_VISIBLE) == 'ok', 'barre hors de la vue à %s : %s' % (nom, p.evaluate(BARRE_VISIBLE)))
            verifier(p.evaluate(CHEVAUCHEMENT) == 'ok', 'chevauchement à %s : %s' % (nom, p.evaluate(CHEVAUCHEMENT)))
            verifier(p.evaluate(SANS_DEFILEMENT), 'défilement horizontal à %s' % nom)
            ancre = p.locator('dialog.f96-fenetre.v101-ancre[open]').count() == 1
            verifier(ancre == (vue['width'] >= 1200), 'mode inattendu à %s (ancré : %s)' % (nom, ancre))
            p.keyboard.press('Escape')
        s.propre('volet : tailles')

        # 11. 390 px : fenêtre flottante, plein écran, barre visible, aucun défilement horizontal
        p.set_viewport_size({'width': 390, 'height': 844})
        s.aller('/aujourdhui?vue=file')
        ouvrir(p, '#f100-file a[data-f96-action="reponse"]')
        verifier(p.locator('dialog.f96-fenetre.v101-ancre[open]').count() == 0, 'volet ancré à 390 px')
        verifier(p.evaluate(BARRE_VISIBLE) == 'ok', 'barre hors de la vue à 390 px : %s' % p.evaluate(BARRE_VISIBLE))
        verifier(p.evaluate(SANS_DEFILEMENT), 'défilement horizontal à 390 px')
        p.frame_locator('dialog.f96-fenetre[open] iframe[data-v101-cadre="envoi"]').locator('p').first.wait_for(timeout=10000)
        if sortie:
            p.screenshot(path=str(sortie / 'P101v-flottant-390.png'), mask=[p.locator('.ws-user')])
        s.propre('volet : 390 px')
        verifier(banc.modele.nombre() == 0, 'le modèle a été appelé (%d)' % banc.modele.nombre())
        verifier(all(u.startswith(OFFICE) for u in servis), 'requête inattendue vers le serveur de documents')
    finally:
        p.unroute(OFFICE + '/**')
        regler_office(banc, False)
        p.set_viewport_size({'width': 1366, 'height': 900})


