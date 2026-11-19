"""5.6.106 (lot R) : parcours P106r du banc ``scripts/parcours-navigateur.py`` (vrai navigateur, données fictives, aucun réseau, aucun modèle).

P106r — rédaction d'un courriel dans « Courriels », avec la fausse messagerie IMAP du lot L (``tests/faux_imap56106.py``, complétée ici
d'APPEND pour les brouillons, dans le processus du banc seulement) :
- compacité, mesurée à 100 % de zoom à 1920 × 950 et 1366 × 768 sur « Répondre à tous » : hauteur des champs d'en-tête (≈ 32 px), barre
  d'outils en une ligne, éditeur commençant à 220 px au plus du haut de la colonne et seule zone de défilement (ni la colonne ni la page
  ne défilent), boutons « Envoyer », « Enregistrer le brouillon », « Annuler » visibles sans défiler, texte à 14 px au moins, curseur en
  tête du message au-dessus de la signature ;
- cases ☐ / ☑ (bouton puis clic), menu « Plus » au clavier, adresses proposées (aucune requête avant deux caractères, flèches, Entrée),
  pièces jointes (choix, glisser-déposer, « ✕ », total), dictée désactivée annoncée lisiblement, enregistrement automatique (30 s),
  reprise d'un brouillon (« Modifier »), modèles de réponse (enregistrer puis insérer).
Non inscrit dans ``PARCOURS`` : l'intégrateur l'inscrit et le lance. Lancement seul : ``python scripts/parcours_p106r.py [--avant]``
(``--avant`` : présentation précédente — script 5.6.106 de la rédaction bloqué — mesures seulement, sans exigence).
"""
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
NAV = None
MESURES = {}

MESURE_JS = """() => {
  const form = document.querySelector('form.bx5628-compose'), panel = document.getElementById('bx5625-panel') || (form && form.parentElement);
  if (!form || !panel) return {erreur: 'rédaction absente'};
  const vh = window.innerHeight, top = panel.getBoundingClientRect().top;
  const r = s => { const n = form.querySelector(s); return n && !n.hidden ? n.getBoundingClientRect() : null; };
  const h2 = form.querySelector('.bx5628-compose-head h2');
  const champ = n => { const x = form.querySelector('.bx5628-field input[name="' + n + '"]'); return x && x.closest('.bx5628-field').getBoundingClientRect(); };
  const barre = r('.bx5628-fmt'), ed = r('.bx5628-editor'), act = r('.bx5628-actions');
  const editor = form.querySelector('.bx5628-editor');
  const boutons = Array.from(form.querySelectorAll('.bx5628-fmt > button'));
  const lignesBarre = new Set(boutons.filter(b => b.offsetParent).map(b => Math.round(b.getBoundingClientRect().top))).size;
  let mini = 99;
  form.querySelectorAll('label span, input, button, .ax-muted, h2, .bx5628-editor, .bx56106r-statut, .bx56106r-auto').forEach(n => {
    if (!n.offsetParent || !(n.textContent || n.value || '').trim()) return;
    mini = Math.min(mini, parseFloat(getComputedStyle(n).fontSize));
  });
  const blocs = Array.from(editor.children);
  let vides = 0; for (const b of blocs) { if ((b.textContent || '').trim()) break; vides++; }
  const sig = blocs.find(b => (b.textContent || '').trim());
  const sel = window.getSelection();
  const curseur = sel.rangeCount && editor.contains(sel.anchorNode) ? (blocs.indexOf(sel.anchorNode.nodeType === 1 && sel.anchorNode.parentNode === editor ? sel.anchorNode : (sel.anchorNode.closest ? sel.anchorNode.closest('.bx5628-editor > *') : sel.anchorNode.parentElement.closest('.bx5628-editor > *')))) : -2;
  return {titre_px: h2 ? parseFloat(getComputedStyle(h2).fontSize) : 0,
          champ_a_px: Math.round((champ('to') || {height: 0}).height), champ_objet_px: Math.round((champ('subject') || {height: 0}).height),
          haut_barre_px: barre ? Math.round(barre.top - top) : -1, lignes_barre: lignesBarre,
          debut_editeur_px: ed ? Math.round(ed.top - top) : -1, hauteur_editeur_px: ed ? Math.round(ed.height) : 0,
          blanc_avant_signature_px: sig && ed ? Math.round(sig.getBoundingClientRect().top - ed.top) : -1, lignes_vides_avant_signature: vides,
          curseur_bloc: curseur,
          actions_visibles: !!act && act.bottom <= vh + 1 && act.top >= 0, actions_bas_px: act ? Math.round(vh - act.bottom) : -1,
          defilement_colonne: panel.scrollHeight > panel.clientHeight + 1, defilement_page: document.scrollingElement.scrollHeight > vh + 1,
          police_min_px: mini};
}"""


def _nav():
    if NAV is not None:
        return NAV
    import __main__ as nav
    return nav


def verifier(condition, message):
    _nav().verifier(condition, message)


def _brut_cc():
    lignes = ['From: Claire DURAND <durand@example.test>', 'To: cabinet@example.test, martin@example.test',
              'Cc: bernard@example.test, zorval@example.test', 'Subject: Convention DOS-001 - relecture', 'Message-ID: <r106-1@example.test>',
              'Date: Mon, 16 Nov 2026 09:00:00 +0100', 'MIME-Version: 1.0', 'Content-Type: text/plain; charset=utf-8']
    return ('\r\n'.join(lignes) + '\r\n\r\nBonjour Maître,\r\nVoici la convention pour le dossier DOS-001.\r\n').encode('utf-8')


def preparer(banc):
    """Fausse messagerie (APPEND ajouté ici pour les brouillons), signature du cabinet, contact fictif."""
    sys.path.insert(0, str(ROOT / 'tests'))
    import faux_imap56106 as fi
    from agent import mailbox as mbx
    from agent.common import private_json
    from agent.desk import Desk

    def append(self, boite, drapeaux, quand, brut):
        nom = self._nom(boite)
        if nom not in self.s.boites:
            return 'NO', [b'[TRYCREATE]']
        uid = self.s.ajouter(nom, brut, set(str(drapeaux or '').strip('()').split()))
        return 'OK', [('[APPENDUID %s %d] ok' % (self.s.boites[nom]['validite'], uid)).encode()]
    if not hasattr(fi.FauxIMAP, 'append'):
        fi.FauxIMAP.append = append
    serveur = fi.Serveur(separateur='/')
    serveur.semer(12)
    serveur.ajouter('INBOX', _brut_cc(), set())
    secret = Path(banc.base) / 'donnees' / 'imap.secret'
    secret.write_text('mot-de-passe-fictif-du-banc', encoding='utf-8')
    secret.chmod(0o600)                                   # Linux : read_secret refuse un secret lisible par d'autres
    banc.config['mail'].update({'enabled': True, 'username': 'cabinet', 'host': 'mail.example.test', 'password_file': str(secret), 'trash': 'Trash',
                                'drafts': 'Drafts', 'sent': 'Sent', 'from_address': 'cabinet@example.test', 'from_name': 'Cabinet VALBRUNE'})
    private_json(banc.config_path, banc.config)
    desk = Desk(banc.config)
    desk.setting('courrier5632:prefs', {'signature_html': '<p>Maître KERVILAC<br>Avocat au barreau de Valbrune<br>01 00 00 00 05</p>',
                                        'font': 'arial', 'size': 11, 'sign': {'nouveau': True, 'repondre': True, 'transferer': True}})
    from agent import crm5636
    crm5636.save(desk, {'name': 'DURAND', 'first_name': 'Claire', 'email': 'durand@example.test', 'category': 'client'})
    crm5636.save(desk, {'name': 'ORLANDEC', 'first_name': 'Paul', 'email': 'orlandec@example.test', 'category': 'confrere'})
    banc._connect_origine = mbx.connect
    mbx.connect = serveur.connexion
    return serveur


def restaurer(banc):
    from agent import mailbox as mbx
    if getattr(banc, '_connect_origine', None) is not None:
        mbx.connect = banc._connect_origine


def ouvrir_repondre_tous(s):
    p = s.page
    s.aller('/courriels')
    p.wait_for_selector('#bx5625-rows .ax-row[data-uid]', timeout=15000)
    p.locator('#bx5625-rows .ax-row[data-uid]', has_text='Convention DOS-001').first.click()
    p.wait_for_selector('.bx5628-bar', timeout=15000)
    p.locator('.bx5628-bar button', has_text='Répondre à tous').click()
    p.wait_for_selector('form.bx5628-compose .bx5628-editor', timeout=15000)
    p.wait_for_timeout(500)


def mesures(banc, s, exiger=True):
    p = s.page
    sortie = Path(getattr(banc, 'sortie', '.') or '.')
    if getattr(banc, 'avant', False):   # présentation précédente : script de la rédaction 5.6.106 bloqué (repli de v5628.js)
        p.route('**/static/v56106-redaction.js*', lambda route: route.abort())
    out = {}
    for largeur, hauteur in ((1920, 950), (1366, 768)):
        p.set_viewport_size({'width': largeur, 'height': hauteur})
        ouvrir_repondre_tous(s)
        m = p.evaluate(MESURE_JS)
        out['%dx%d' % (largeur, hauteur)] = m
        # cas le plus chargé : Cc et Cci ouverts (comme quand une règle les préremplit)
        for b in p.locator('form.bx5628-compose .bx5628-toggles button, form.bx5628-compose .bx5628-field .bx5628-link').all():
            b.click()
        p.wait_for_timeout(300)
        out['%dx%d-cc-cci' % (largeur, hauteur)] = m4 = p.evaluate(MESURE_JS)
        MESURES.update(out)
        p.screenshot(path=str(sortie / ('P106r-redaction-%d-cc-cci%s.png' % (largeur, '-avant' if getattr(banc, 'avant', False) else ''))))
        if exiger:
            verifier(0 <= m4.get('debut_editeur_px', -1) <= 220, '%d × %d, Cc et Cci ouverts : l’éditeur commence à %s px' % (largeur, hauteur, m4.get('debut_editeur_px')))
            verifier(m4.get('actions_visibles') and not m4.get('defilement_page'), '%d × %d, Cc et Cci ouverts : boutons hors de la vue' % (largeur, hauteur))
        p.screenshot(path=str(sortie / ('P106r-redaction-%d%s.png' % (largeur, '-avant' if getattr(banc, 'avant', False) else ''))))
        if exiger:
            nom = '%d × %d' % (largeur, hauteur)
            verifier(m.get('champ_a_px', 99) <= 34 and m.get('champ_objet_px', 99) <= 34, '%s : champs d’en-tête trop hauts (%s)' % (nom, m))
            verifier(m.get('lignes_barre') == 1, '%s : barre d’outils sur %s lignes' % (nom, m.get('lignes_barre')))
            verifier(0 <= m.get('debut_editeur_px', -1) <= 220, '%s : l’éditeur commence à %s px du haut de la colonne' % (nom, m.get('debut_editeur_px')))
            verifier(not m.get('defilement_colonne') and not m.get('defilement_page'), '%s : la colonne ou la page défile (%s)' % (nom, m))
            verifier(m.get('actions_visibles'), '%s : boutons d’envoi hors de la vue' % nom)
            verifier(m.get('police_min_px', 0) >= 14, '%s : texte à %s px' % (nom, m.get('police_min_px')))
            verifier(m.get('curseur_bloc') == 0, '%s : curseur hors de la première ligne (%s)' % (nom, m.get('curseur_bloc')))
            verifier(m.get('lignes_vides_avant_signature') == 1, '%s : %s lignes vides avant la signature' % (nom, m.get('lignes_vides_avant_signature')))
    if getattr(banc, 'avant', False):
        p.unroute('**/static/v56106-redaction.js*')
    MESURES.update(out)
    return out


def p106r_redaction(banc, s):
    serveur = preparer(banc)
    try:
        s.connecter()
        mesures(banc, s, exiger=not getattr(banc, 'avant', False))
        if getattr(banc, 'avant', False):
            return
        s.propre('compacité de la rédaction')
        _fonctions(banc, s, serveur)
    finally:
        restaurer(banc)


def _fonctions(banc, s, serveur):
    p = s.page
    sortie = Path(getattr(banc, 'sortie', '.') or '.')
    p.set_viewport_size({'width': 1920, 'height': 950})
    ouvrir_repondre_tous(s)
    form = p.locator('form.bx5628-compose')
    verifier(form.locator('.bx5628-options-envoi[data-lot="E"]').count() == 1, 'zone « Options d’envoi » du lot E absente')
    editor = form.locator('.bx5628-editor')
    # cases ☐ / ☑ : bouton « Case » puis clic sur la case
    p.keyboard.type('Pièces à réunir')
    form.locator('button[title^="Case à cocher"]').click()
    verifier('☐ Pièces à réunir' in editor.inner_text(), 'la case ☐ n’est pas insérée en début de ligne')
    boite = p.evaluate("""() => { const ed = document.querySelector('.bx5628-editor'); const w = document.createTreeWalker(ed, NodeFilter.SHOW_TEXT);
      let n; while ((n = w.nextNode())) { const i = n.data.indexOf('☐'); if (i >= 0) { const r = document.createRange(); r.setStart(n, i); r.setEnd(n, i + 1);
      const b = r.getBoundingClientRect(); return {x: b.left + b.width / 2, y: b.top + b.height / 2}; } } return null; }""")
    p.mouse.click(boite['x'], boite['y'])
    verifier('☑ Pièces à réunir' in editor.inner_text(), 'le clic sur la case ne la coche pas')
    # menu « Plus » au clavier
    plus = form.locator('button[title^="Plus de mise en forme"]')
    plus.focus()
    p.keyboard.press('Enter')
    verifier(form.locator('.bx56106r-menu[aria-label="Plus de mise en forme"]').is_visible(), 'menu « Plus » non ouvert au clavier')
    p.keyboard.press('ArrowDown')
    p.keyboard.press('Escape')
    verifier(not form.locator('.bx56106r-menu[aria-label="Plus de mise en forme"]').is_visible(), 'Échap ne ferme pas le menu « Plus »')
    verifier(p.evaluate('document.activeElement.title').startswith('Plus de mise en forme'), 'le focus ne revient pas au bouton « Plus »')
    # couleur, centrage, titre, ligne et date depuis le menu « Plus » (politique de sécurité respectée : contrôlé par s.propre)
    p.evaluate("""() => { const ed = document.querySelector('.bx5628-editor'), w = document.createTreeWalker(ed, NodeFilter.SHOW_TEXT); let n;
      while ((n = w.nextNode())) { const i = n.data.indexOf('Pièces'); if (i >= 0) { const r = document.createRange(); r.setStart(n, i); r.setEnd(n, i + 6);
      const s = getSelection(); s.removeAllRanges(); s.addRange(r); return; } } }""")
    for titre in ('Couleur : Rouge', 'Centrer'):
        plus.click()
        form.locator('.bx56106r-menu[aria-label="Plus de mise en forme"] button[title^="%s"]' % titre).click()
    verifier(p.evaluate("() => Array.from(document.querySelectorAll('.bx5628-editor *')).some(n => getComputedStyle(n).color === 'rgb(180, 35, 24)')"),
             'couleur rouge non appliquée')
    verifier(p.evaluate("() => Array.from(document.querySelectorAll('.bx5628-editor p')).some(n => getComputedStyle(n).textAlign === 'center')"),
             'centrage non appliqué')
    p.evaluate("() => { const s = getSelection(); s.collapseToEnd(); }")
    for titre in ('Ligne horizontale', 'Insérer la date du jour'):
        plus.click()
        form.locator('.bx56106r-menu[aria-label="Plus de mise en forme"] button[title^="%s"]' % titre).click()
    verifier(editor.locator('hr').count() >= 1, 'ligne horizontale absente')
    verifier(p.evaluate("() => Array.from(document.querySelectorAll('.bx5628-editor *')).some(n => getComputedStyle(n).color === 'rgb(180, 35, 24)')"),
             'la couleur a disparu après la ligne et la date')
    s.propre('cases et menu « Plus »')
    # dictée : désactivée sur le banc → message lisible, aucun micro ouvert
    form.locator('.bx56106r-barre .bx56106r-mic').click()
    p.wait_for_timeout(200)
    verifier('Dictée désactivée' in form.locator('.bx56106r-barre > .bx56106r-statut').inner_text(), 'dictée désactivée non annoncée')
    # adresses proposées : rien avant deux caractères, puis flèches et Entrée
    demandes = []
    p.on('request', lambda r: demandes.append(r.url) if 'm5636/contacts' in r.url else None)
    a = form.locator('input[name="to"]')
    a.click()
    p.keyboard.press('End')
    p.keyboard.type(', o')
    p.wait_for_timeout(500)
    verifier(not demandes, 'requête de contacts avant deux caractères')
    p.keyboard.type('rl')
    p.wait_for_selector('.bx56106r-adr [role="option"]', timeout=5000)
    verifier(len(demandes) == 1, 'anti-rebond : %d requêtes' % len(demandes))
    p.keyboard.press('ArrowDown')
    p.keyboard.press('ArrowUp')
    p.keyboard.press('Enter')
    verifier('Paul ORLANDEC <orlandec@example.test>' in a.input_value() and a.input_value().count('durand@example.test') == 1, 'adresse proposée non retenue : %s' % a.input_value())
    s.propre('adresses proposées')
    # pièces jointes : choix, glisser-déposer, « ✕ », total
    form.locator('.bx56106r-pj-input').set_input_files([{'name': 'note-DOS-001.txt', 'mimeType': 'text/plain', 'buffer': b'Note fictive'}])
    p.evaluate("""() => { const dt = new DataTransfer(); dt.items.add(new File(['bordereau fictif'], 'bordereau.txt', {type: 'text/plain'}));
      const f = document.querySelector('form.bx5628-compose');
      ['dragenter', 'dragover', 'drop'].forEach(t => f.dispatchEvent(new DragEvent(t, {bubbles: true, cancelable: true, dataTransfer: dt}))); }""")
    verifier(form.locator('.bx56106r-pj-item').count() == 2, 'pièces : %d au lieu de 2' % form.locator('.bx56106r-pj-item').count())
    verifier('2 pièces' in form.locator('.bx56106r-pj-total').inner_text() and '15 Mo' in form.locator('.bx56106r-pj-total').inner_text(), 'total des pièces absent')
    form.locator('.bx56106r-pj-x').nth(1).click()
    verifier(form.locator('.bx56106r-pj-item').count() == 1, '« ✕ » ne retire pas la pièce')
    # images : collage d'une image PNG (insérée en ligne), image distante collée (remplacée par un message), outils de largeur
    editor.click()
    p.evaluate("""() => { const b = atob('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==');
      const u = new Uint8Array(b.length); for (let i = 0; i < b.length; i++) u[i] = b.charCodeAt(i);
      const dt = new DataTransfer(); dt.items.add(new File([u], 'capture.png', {type: 'image/png'}));
      document.querySelector('.bx5628-editor').dispatchEvent(new ClipboardEvent('paste', {clipboardData: dt, bubbles: true, cancelable: true})); }""")
    p.wait_for_function("() => document.querySelectorAll('.bx5628-editor img[src^=\"data:image/png\"]').length === 1", timeout=5000)
    p.evaluate("""() => { const dt = new DataTransfer(); dt.setData('text/html', '<p>Plan <img src="https://tiers.example.test/suivi.png" onerror="x()"></p>');
      document.querySelector('.bx5628-editor').dispatchEvent(new ClipboardEvent('paste', {clipboardData: dt, bubbles: true, cancelable: true})); }""")
    p.wait_for_timeout(200)
    verifier(editor.locator('img[src^="https:"]').count() == 0 and 'Image distante non insérée' in editor.inner_text(), 'image distante non remplacée')
    # HTML collé avec attributs d'événement : assaini hors de la page, aucun attribut on… n'atteint le document vivant
    p.evaluate("""() => { const dt = new DataTransfer(); dt.setData('text/html', '<p onclick="x()">Note <b onmouseover="y()">DOS-001</b> <a href="javascript:z()">lien</a><svg onload="w()"></svg></p>');
      document.querySelector('.bx5628-editor').dispatchEvent(new ClipboardEvent('paste', {clipboardData: dt, bubbles: true, cancelable: true})); }""")
    p.wait_for_timeout(200)
    verifier(p.evaluate("() => Array.from(document.querySelectorAll('.bx5628-editor *')).every(n => !Array.from(n.attributes).some(a => /^on/i.test(a.name)) && !/^javascript:/i.test(n.getAttribute('href') || ''))"),
             'attribut d’événement ou lien javascript: inséré dans l’éditeur')
    verifier('Note DOS-001' in editor.inner_text().replace('\n', ' ') or 'Note' in editor.inner_text(), 'texte collé absent')
    editor.locator('img').first.dispatch_event('click')   # image de 1 px : clic simulé sur l'élément
    form.locator('.bx56106r-imgbar button', has_text='Moyenne').click()
    verifier(editor.locator('img').first.get_attribute('width') == '400', 'largeur fixe de l’image non appliquée')
    verifier('image' in form.locator('.bx56106r-pj-total').inner_text(), 'image non comptée dans le total')
    s.propre('images collées')
    # enregistrement : pièces locales remplacées par celles du brouillon, état « Enregistré à … »
    with p.expect_response(lambda r: '/m5628/brouillon' in r.url and r.request.method == 'POST') as rep:
        form.locator('.bx5628-actions button', has_text='Enregistrer le brouillon').click()
    corps = rep.value.json()
    verifier(corps.get('uid') and [x['name'] for x in corps.get('pieces', [])] == ['note-DOS-001.txt'], 'brouillon : %s' % corps)
    import email
    from email import policy as _policy
    brut = serveur.boites['Drafts']['uids'][int(corps['uid'])][1]
    html = email.message_from_bytes(brut, policy=_policy.default).get_body(preferencelist=('html',)).get_content()
    verifier('src="cid:' in html and 'data:' not in html and 'tiers.example.test' not in html, 'image du brouillon non intégrée en cid')
    p.wait_for_timeout(300)
    verifier(form.locator('.bx56106r-auto').inner_text().startswith('Enregistré à'), 'état d’enregistrement absent')
    brouillons = serveur.boites['Drafts']['uids']
    verifier(len(brouillons) == 1, 'un brouillon attendu dans Brouillons (%d)' % len(brouillons))
    # enregistrement automatique : un changement, puis 30 s
    editor.click()
    p.keyboard.type(' Ajout pour l’enregistrement automatique.')
    with p.expect_response(lambda r: '/m5628/brouillon' in r.url and r.request.method == 'POST', timeout=40000) as rep2:
        pass
    verifier(rep2.value.ok, 'enregistrement automatique en échec')
    verifier(len(serveur.boites['Drafts']['uids']) == 1, 'l’enregistrement automatique n’a pas remplacé le brouillon')
    uid = str(rep2.value.json()['uid'])
    s.propre('pièces et enregistrement automatique')
    # modèles : enregistrer le message comme modèle, puis l'insérer
    p.once('dialog', lambda d: d.accept('Accusé de réception DOS-001'))
    form.locator('button[title="Modèles de réponse du cabinet"]').click()
    with p.expect_response(lambda r: '/m56106/redaction/modeles/creer' in r.url):
        form.locator('.bx56106r-menu button', has_text='Enregistrer le message comme modèle').click()
    form.locator('button[title="Modèles de réponse du cabinet"]').click()
    p.wait_for_selector('.bx56106r-menu .bx56106r-mtitre', timeout=5000)
    verifier('Accusé de réception DOS-001' in form.locator('.bx56106r-menu[aria-label="Modèles de réponse"]').inner_text(), 'modèle enregistré absent de la liste')
    p.keyboard.press('Escape')
    s.propre('modèles de réponse')
    # reprise du brouillon : « Modifier » depuis le dossier Brouillons
    barre = p.evaluate("() => Array.from(window.axh5628.toolbar({uid: '1'}, {role: 'drafts', folders: []}).querySelectorAll('button')).map(b => b.textContent)")
    verifier(any('Modifier' in x for x in barre), '« Modifier » absent de la barre d’un brouillon')
    p.evaluate("([uid, v]) => window.axh5628.compose('brouillon', {uid: uid, folder: 'Drafts', uidvalidity: v}, window.axh5625Ctx())",   # 5.6.108 (B02) : génération transmise
               [uid, str(serveur.boites['Drafts']['validite'])])
    p.wait_for_selector('form.bx5628-compose .bx5628-editor', timeout=15000)
    form = p.locator('form.bx5628-compose')
    verifier('Brouillon' in form.locator('h2').inner_text(), 'titre du brouillon rouvert')
    verifier('Convention DOS-001' in form.locator('input[name="subject"]').input_value(), 'objet du brouillon non repris')
    verifier('Ajout pour l’enregistrement automatique' in form.locator('.bx5628-editor').inner_text(), 'corps du brouillon non repris')
    verifier(form.locator('.bx5628-editor img[src^="data:image/png"]').count() == 1, 'image du brouillon non réaffichée')
    verifier(form.locator('.bx56106r-pj-item').count() == 1, 'pièce du brouillon non reprise')
    with p.expect_response(lambda r: '/m5628/brouillon' in r.url and r.request.method == 'POST') as rep3:
        form.locator('.bx5628-actions button', has_text='Enregistrer le brouillon').click()
    verifier(int(uid) not in serveur.boites['Drafts']['uids'] and len(serveur.boites['Drafts']['uids']) == 1, 'le brouillon rouvert n’a pas été remplacé')
    verifier([x['name'] for x in rep3.value.json().get('pieces', [])] == ['note-DOS-001.txt'], 'pièce perdue à la reprise')
    p.screenshot(path=str(sortie / 'P106r-redaction-reprise.png'))
    s.propre('reprise d’un brouillon')


def main(argv=None):
    """Lancement seul (sans inscription dans ``PARCOURS``) : banc chargé comme module, parcours ajouté, mesures écrites dans la sortie."""
    global NAV
    import argparse
    import importlib.util
    import tempfile
    parser = argparse.ArgumentParser()
    parser.add_argument('--avant', action='store_true')
    parser.add_argument('--sortie', default=str(Path(tempfile.gettempdir()) / 'axiorhub-parcours-p106r'))
    parser.add_argument('--navigateur', default='chromium')
    args = parser.parse_args(argv)
    sys.path.insert(0, str(ROOT))
    spec = importlib.util.spec_from_file_location('parcours_navigateur', str(ROOT / 'scripts' / 'parcours-navigateur.py'))
    NAV = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(NAV)
    Banc = NAV.Banc

    class BancP106r(Banc):
        avant = args.avant
    NAV.Banc = BancP106r
    NAV.PARCOURS = NAV.PARCOURS + (('P106r', 'Courriels : rédaction compacte, dictée, mise en forme, adresses, pièces, brouillons, modèles', p106r_redaction),)
    code = NAV.lancer(args.navigateur, args.sortie, ('P106r',))
    Path(args.sortie).mkdir(parents=True, exist_ok=True)
    (Path(args.sortie) / ('P106r-mesures%s.json' % ('-avant' if args.avant else ''))).write_text(json.dumps(MESURES, ensure_ascii=False, indent=1), encoding='utf-8')
    print(json.dumps(MESURES, ensure_ascii=False, indent=1))
    return code


if __name__ == '__main__':
    sys.exit(main())
