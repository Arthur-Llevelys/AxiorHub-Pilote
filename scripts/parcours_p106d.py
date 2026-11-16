"""5.6.106 (lot D) : parcours P106d du banc ``scripts/parcours-navigateur.py`` (vrai navigateur, données fictives, aucun réseau, aucun modèle).

P106d — densité de toutes les pages et identité du cabinet :
- cabinet fictif « Cabinet DURAND Avocats » (profil du cabinet), logo carré fictif généré ici, déposé par Paramètres › Démarrage du cabinet
  comme l'administrateur (champ fichier, envoi, rechargement) ;
- 1920 × 950 : barre latérale avec « [logo] Cabinet DURAND Avocats » en tête et le menu qui commence plus haut qu'en 5.6.105 (116 px) ;
  barre du haut de 48 px au plus ; titre et onglets de Produire sur une bande ; « Trier par » de Dossiers sur une ligne ; filtres
  d'À relire sur une ligne ; boutons de 32 px ; aucun défilement horizontal ; aucun bandeau d'identité dans la page Aujourd'hui ;
- mesures avant contenu et lignes visibles à 1920 × 950 et 1366 × 768 sur Aujourd'hui, Dossiers, Agenda, Produire, À relire et
  Messagerie instantanée (rapport ``P106d-mesures.json`` dans le dossier de sortie) ;
- 390 px : aucun défilement horizontal, boutons du contenu de 40 px au moins ; 200 % (960 × 475) : aucun défilement horizontal ;
- sombre : captures ; Mon compte › Densité « Confortable » : ``data-densite="confortable"`` ; retrait du logo : pastille aux initiales.
Non inscrit dans ``PARCOURS`` : l'intégrateur l'inscrit et le lance.
"""
import json
from pathlib import Path
import struct
import zlib

SANS_DEFILEMENT = 'document.documentElement.scrollWidth <= window.innerWidth + 1'
PAGES = (('Aujourd’hui', '/aujourdhui', '#ax95-actuellement', '#f96-decider li, .c5662-file li'),
         ('Dossiers', '/dossiers', 'table.d97-table', 'table.d97-table tbody tr'),
         ('Agenda', '/planning', '[data-ag97-grille]', '@agenda'),
         ('Produire', '/production', '#p100-documents', '.q51-item'),
         ('À relire', '/a-relire', 'nav.ax100-liste', 'nav.ax100-liste li'),
         ('Messagerie instantanée', '/messagerie', '#m50-chat', '.m50-side li'))
MESURE = """([contenu, lignes]) => {
  const c = document.querySelector(contenu);
  let visibles = 0, hauteur = null;
  if (lignes === '@agenda') {
    const g = document.querySelector('[data-ag97-grille]'); const cases = [...document.querySelectorAll('.ag97-case:not(.ag97-jcase)')];
    if (g && cases.length) { const p = cases[0].getBoundingClientRect(); hauteur = Math.round(p.height);
      visibles = Math.max(0, Math.floor((Math.min(innerHeight, g.getBoundingClientRect().bottom) - p.top) / p.height)); }
  } else {
    const ls = [...document.querySelectorAll(lignes)].filter(x => x.getBoundingClientRect().height > 0);
    visibles = ls.filter(x => { const r = x.getBoundingClientRect(); return r.top >= 0 && r.bottom <= innerHeight + 0.5; }).length;
    if (ls.length) hauteur = Math.round(ls[0].getBoundingClientRect().height);
  }
  const tb = document.querySelector('.ws-topbar'), nav = document.querySelector('#ws-sidebar .ws-primary a');
  return {avant_contenu: c ? Math.round(c.getBoundingClientRect().top + scrollY) : null, lignes_visibles: visibles, hauteur_ligne: hauteur,
          entete: tb ? Math.round(tb.getBoundingClientRect().height) : null, menu_haut: nav ? Math.round(nav.getBoundingClientRect().top) : null,
          densite: document.body.dataset.densite || ''};
}"""
CIBLES_390 = """() => [...document.querySelectorAll('#ws-main .ax-btn, #ws-main button')].filter(b => { const r = b.getBoundingClientRect();
  return r.width > 0 && r.height > 0 && getComputedStyle(b).visibility !== 'hidden'; }).filter(b => b.getBoundingClientRect().height < 39.5)
  .map(b => (b.textContent || b.getAttribute('aria-label') || '').trim().slice(0, 30) + ' : ' + Math.round(b.getBoundingClientRect().height)).slice(0, 8)"""


def _nav():
    import __main__ as nav
    return nav


def logo_fictif(cote=128):
    """Logo carré fictif (aucune donnée réelle) : fond bleu nuit, carré doré au centre."""
    lignes = b''.join(b'\x00' + b''.join(bytes((240, 196, 80)) if cote * .28 < x < cote * .72 and cote * .28 < y < cote * .72 else bytes((23, 50, 77))
                                         for x in range(cote)) for y in range(cote))

    def morceau(t, d):
        return struct.pack('>I', len(d)) + t + d + struct.pack('>I', zlib.crc32(t + d) & 0xffffffff)
    return b'\x89PNG\r\n\x1a\n' + morceau(b'IHDR', struct.pack('>IIBBBBB', cote, cote, 8, 2, 0, 0, 0)) + morceau(b'IDAT', zlib.compress(lignes, 9)) + morceau(b'IEND', b'')


def semer(banc):
    import parcours_p100 as p100
    import parcours_p97a as p97a
    from agent import cabinet_profile5689 as profil
    p100.semer_propositions(banc, n=24)
    p97a.semer(banc)
    with banc.desk() as d:
        profil.enregistrer(d, 'banc', 'administrateur', {'revision': profil.lire(d)['revision'], 'nom_cabinet': 'Cabinet DURAND Avocats'})


def p106d_densite(banc, s):
    nav = _nav()
    verifier = nav.verifier
    p = s.page
    sortie = Path(getattr(banc, 'sortie', '.') or '.')
    semer(banc)
    p.set_viewport_size({'width': 1920, 'height': 950})
    s.connecter()
    # dépôt du logo par Paramètres, comme l'administrateur
    s.aller('/parametres?rubrique=demarrage')
    zone = p.locator('#d106-logo')
    verifier(zone.count() == 1, 'réglage « Logo du cabinet » absent de Paramètres › Démarrage du cabinet')
    fichier = sortie / 'P106d-logo-fictif.png'
    fichier.write_bytes(logo_fictif())
    with p.expect_navigation(timeout=15000):
        zone.locator('input[type=file]').set_input_files(str(fichier))
    p.wait_for_load_state('networkidle')
    verifier(p.locator('#ws-sidebar img.id106-marque').count() == 1, 'logo absent de la barre latérale après le dépôt')
    s.propre('dépôt du logo')
    mesures = {}
    for vw, vh in ((1920, 950), (1366, 768)):
        p.set_viewport_size({'width': vw, 'height': vh})
        for nom, chemin, contenu, lignes in PAGES:
            s.aller(chemin)
            m = p.evaluate(MESURE, [contenu, lignes])
            mesures['%s @%dx%d' % (nom, vw, vh)] = m
            verifier(m['densite'] == 'compacte', '%s : densité %r au lieu de « compacte »' % (nom, m['densite']))
            verifier(m['entete'] is not None and m['entete'] <= 48, '%s : barre du haut de %s px (48 au plus)' % (nom, m['entete']))
            verifier(m['menu_haut'] is not None and m['menu_haut'] < 116, '%s : le menu commence à %s px (116 en 5.6.105)' % (nom, m['menu_haut']))
            verifier(p.evaluate(SANS_DEFILEMENT), '%s : défilement horizontal à %d px' % (nom, vw))
            s.propre('%s à %d px' % (nom, vw))
            if vw == 1920:
                s.capture(sortie / ('P106d-%s-1920.png' % chemin.strip('/')))
    (sortie / 'P106d-mesures.json').write_text(json.dumps(mesures, ensure_ascii=False, indent=1), encoding='utf-8')
    p.set_viewport_size({'width': 1920, 'height': 950})
    # Aujourd'hui : le titre reste, aucun bandeau d'identité dans la page
    s.aller('/aujourdhui')
    verifier(p.locator('#ws-main [data-id106]').count() == 0, 'bandeau d’identité dans la page Aujourd’hui')
    verifier(p.locator('#ws-main h1', has_text='Aujourd’hui').count() == 1, 'titre « Aujourd’hui » absent')
    nom = p.locator('#ws-sidebar .id106-nom')
    verifier(nom.count() == 1 and 'Cabinet DURAND Avocats' in nom.inner_text(), 'nom du cabinet absent en tête de la barre latérale')
    # Produire : titre et onglets sur une bande ; boutons de 32 px
    s.aller('/production')
    bande = p.locator('#ws-main .page-title.ax106-bande')
    verifier(bande.count() == 1 and bande.locator('nav.ws-tabs').count() == 1, 'titre et onglets de Produire pas sur une bande')
    h = p.evaluate("() => Math.round(document.querySelector('#ws-main .ax-btn').getBoundingClientRect().height)")
    verifier(h <= 32, 'bouton de %d px (32 attendus)' % h)
    # Dossiers : « Trier par », sa liste et son bouton sur une ligne
    s.aller('/dossiers')
    tops = p.evaluate("() => [...document.querySelectorAll('form.d100-tri > label, form.d100-tri > select')].map(x => Math.round(x.getBoundingClientRect().top + x.getBoundingClientRect().height / 2))")
    verifier(len(tops) == 2 and abs(tops[0] - tops[1]) <= 4, '« Trier par » et sa liste pas sur une ligne : %s' % tops)
    # À relire : filtres sur une ligne
    s.aller('/a-relire')
    lignes = p.evaluate("() => new Set([...document.querySelectorAll('.ax100-tete nav.ax100-filtres:not(.ax101-sous-filtres) a')].map(a => Math.round(a.getBoundingClientRect().top))).size")
    verifier(lignes == 1, 'filtres d’À relire sur %d lignes' % lignes)
    # sombre
    for schema in ('dark', 'light'):
        p.emulate_media(color_scheme=schema)
        s.aller('/aujourdhui')
        s.capture(sortie / ('P106d-identite-%s.png' % schema))
    s.propre('captures claire et sombre')
    # 390 px et 200 %
    for vue, nom_vue in (({'width': 390, 'height': 844}, '390 px'), ({'width': 960, 'height': 475}, '200 %')):
        p.set_viewport_size(vue)
        for _, chemin, _, _ in PAGES:
            s.aller(chemin)
            verifier(p.evaluate(SANS_DEFILEMENT), '%s : défilement horizontal à %s' % (chemin, nom_vue))
            if nom_vue == '390 px':
                petits = p.evaluate(CIBLES_390)
                verifier(not petits, '%s à 390 px : cibles de moins de 40 px : %s' % (chemin, petits))
        s.propre(nom_vue)
    p.set_viewport_size({'width': 1920, 'height': 950})
    # Mon compte : densité confortable
    s.aller('/mon-compte')
    carte = p.locator('#d106-densite')
    verifier(carte.count() == 1, 'réglage « Densité d’affichage » absent de Mon compte')
    carte.locator('input[value=confortable]').check()
    nav.envoyer(p, 'm56106/densite', lambda: carte.locator('button[type=submit]').click(), 'densité confortable')
    s.aller('/dossiers')
    verifier(p.evaluate("document.body.dataset.densite") == 'confortable', 'la densité confortable n’est pas appliquée')
    # retrait du logo : pastille aux initiales
    s.aller('/parametres?rubrique=demarrage')
    p.once('dialog', lambda d: d.accept())
    with p.expect_navigation(timeout=15000):
        p.locator('#d106-logo [data-d106-retirer]').click()
    p.wait_for_load_state('networkidle')
    verifier(p.locator('#ws-sidebar .id106-initiales').count() == 1, 'pastille aux initiales absente après le retrait du logo')
    s.capture(sortie / 'P106d-sans-logo.png')
    s.propre('retrait du logo')
