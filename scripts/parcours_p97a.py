"""5.6.97 (lot agenda) : parcours P97a du banc ``scripts/parcours-navigateur.py`` (vrai navigateur, données fictives, aucun réseau).

Chargé par le banc ; utilise ses outils (``verifier``) par le module principal. Le résultat métier est vérifié dans la base du banc. Aucun modèle
n'est appelé : le faux fournisseur compte ses appels et le parcours exige zéro.

- P97a1 Grille à 1920 × 950 (zone utile mesurée d'Edge maximisé sur 1920 × 1080 à 100 %) : semaine chargée, barre, jours, ligne « Journée
  entière » et tous les créneaux visibles ; ni la page ni la grille ne défilent pour la plage affichée ; premier et dernier créneaux cliquables.
- P97a2 Saisie : clic sur un créneau libre (éditeur prérempli, Échap rend le focus), glisser de 16 h à 17 h 30 puis Enregistrer en double clic
  (un seul événement), équivalent clavier (flèches, Maj + flèche, Entrée).
- P97a3 125 %, 200 %, mobile et sombre : navigation et « + Nouveau » visibles, aucun défilement horizontal, aucune erreur de console.
"""
from datetime import date, timedelta
from pathlib import Path


def _nav():
    import __main__ as nav
    return nav


SEMAINE = [(9, 0, 'Rendez-vous DURAND', 'DOS-001'), (10, 0, 'Audience fictive Valbrune', 'DOS-001'), (10, 30, 'Appel MARTIN', 'DOS-002'),
           (10, 30, 'Point ZORVAL', 'DOS-003'), (10, 30, 'Relecture KERVILAC', ''), (12, 30, 'Repas ORLANDEC', ''), (14, 0, 'Conclusions RG 24/01234', 'DOS-001'),
           (16, 0, 'Rendez-vous BERNARD', 'DOS-002'), (18, 0, 'Point cabinet', '')]

MESURE = """() => {
  const g = document.querySelector('[data-ag97-grille]'); const r = g.getBoundingClientRect();
  const cases = [...document.querySelectorAll('.ag97-case:not(.ag97-jcase)')];
  const prem = cases[0].getBoundingClientRect(), der = cases[cases.length - 1].getBoundingClientRect();
  const barre = document.querySelector('.ag97-barre').getBoundingClientRect();
  return {vh: innerHeight, vw: innerWidth, haut: r.top, bas: r.bottom, premier: prem.top, dernier: der.bottom, hauteurCase: prem.height,
          barre: barre.top, barreBas: barre.bottom, defilementH: document.scrollingElement.scrollWidth > innerWidth + 1,
          grilleDefile: g.scrollHeight > g.clientHeight + 1, nouveau: !!document.querySelector('[data-ag97-nouveau]').offsetParent};
}"""


def semer(banc):
    from agent import agenda5697
    lundi = date.today() - timedelta(days=date.today().weekday())
    with banc.desk() as d:
        agenda5697.schema(d)
        n = 0
        for i, (h, m, titre, dossier) in enumerate(SEMAINE):
            for k in range(5):
                if (i + k) % 2:
                    continue
                n += 1
                agenda5697.creer(d, ('agent_cabinet', 'agent'), {'cle': 'banc-p97a-semaine-%04d' % n, 'titre': titre, 'nature': 'repas' if 'Repas' in titre else 'rendez_vous',
                                                                   'debut': '%sT%02d:%02d' % (lundi + timedelta(days=k), h, m), 'duree_minutes': 120 if i == 6 else 60,
                                                                   'dossier': dossier})
        agenda5697.creer(d, ('agent_cabinet', 'agent'), {'cle': 'banc-p97a-journee-0001', 'titre': 'Séminaire du cabinet', 'journee_entiere': True,
                                                           'debut': str(lundi + timedelta(days=2))})
        agenda5697.creer(d, ('agent_cabinet', 'agent'), {'cle': 'banc-p97a-tache-00001', 'objet': 'tache', 'titre': 'Préparer le bordereau de pièces',
                                                           'tache': {'reserver': False}})
    return lundi


def p97a_agenda(banc, s):
    nav = _nav()
    verifier = nav.verifier
    lundi = semer(banc)
    p = s.page
    p.set_viewport_size({'width': 1920, 'height': 950})
    s.connecter()
    s.aller('/planning?vue=agenda&agenda=personnel&view=week&date=' + lundi.isoformat())
    p.wait_for_selector('[data-ag97][data-ag97-pret]')
    m = p.evaluate(MESURE)
    verifier(m['bas'] <= m['vh'] and m['dernier'] <= m['vh'] and m['premier'] >= 0, 'P97a1 : la grille ne tient pas dans 1920 × 950 : %s' % m)
    verifier(not m['grilleDefile'] and not m['defilementH'], 'P97a1 : défilement de la grille ou horizontal : %s' % m)
    verifier(m['hauteurCase'] >= 13, 'P97a1 : créneaux trop petits pour être cliqués : %s' % m)
    verifier(p.locator('.ag97-case[data-minute="480"]').count() == 5 and p.locator('.ag97-case[data-minute="1110"]').count() == 5,
             'P97a1 : premier ou dernier créneau absent')
    if getattr(banc, 'sortie', None):
        p.screenshot(path=str(Path(banc.sortie) / 'P97a-grille-1920x950.png'))
    s.propre('grille à 1920 × 950')
    # clic sur un créneau libre, Échap
    libre = p.locator('.ag97-case[data-jour="%s"][data-minute="900"]' % (lundi + timedelta(days=1)))
    libre.click()
    p.wait_for_selector('#ag97-editeur[open]')
    verifier(p.input_value('#ag97-editeur input[name=debut]') == '%sT15:00' % (lundi + timedelta(days=1)) and
             p.input_value('#ag97-editeur input[name=fin]') == '%sT15:30' % (lundi + timedelta(days=1)), 'P97a2 : éditeur mal prérempli au clic')
    p.keyboard.press('Escape')
    p.wait_for_timeout(150)
    verifier(p.evaluate('!document.querySelector("#ag97-editeur").open && document.activeElement.classList.contains("ag97-case")'),
             'P97a2 : Échap ne ferme pas l’éditeur ou ne rend pas le focus au créneau')
    # glisser de 16 h à 17 h 30 le lundi, puis double clic sur Enregistrer
    a = p.locator('.ag97-case[data-jour="%s"][data-minute="960"]' % lundi).bounding_box()
    b = p.locator('.ag97-case[data-jour="%s"][data-minute="1020"]' % lundi).bounding_box()
    p.mouse.move(a['x'] + 6, a['y'] + 3)
    p.mouse.down()
    p.mouse.move(b['x'] + 6, b['y'] + 3, steps=8)
    p.mouse.up()
    p.wait_for_selector('#ag97-editeur[open]')
    verifier(p.input_value('#ag97-editeur input[name=fin]') == '%sT17:30' % lundi, 'P97a2 : le glisser ne donne pas 16 h – 17 h 30')
    p.fill('#ag97-editeur input[name=titre]', 'Rendez-vous glissé DURAND')
    with p.expect_navigation(timeout=15000):
        with p.expect_response(lambda r: '/m5689/agenda/evenements' in r.url and r.request.method == 'POST') as rep:
            p.dblclick('#ag97-editeur [data-ag97-enregistrer]')
    verifier(rep.value.status in (200, 201), 'P97a2 : création refusée (HTTP %d)' % rep.value.status)
    p.wait_for_selector('[data-ag97][data-ag97-pret]')
    with banc.desk() as d:
        n = d.db.execute("SELECT COUNT(*) FROM agenda5634 WHERE title='Rendez-vous glissé DURAND'").fetchone()[0]
    verifier(n == 1, 'P97a2 : double clic : %d événement(s) au lieu d’un' % n)
    verifier(p.locator('.ag97-ev', has_text='Rendez-vous glissé DURAND').count() == 1, 'P97a2 : l’événement créé n’est pas affiché une fois après rechargement')
    # clavier : flèche, Maj + flèche, Entrée
    p.locator('.ag97-case[data-jour="%s"][data-minute="480"]' % lundi).focus()
    p.keyboard.press('ArrowRight')
    p.keyboard.press('Shift+ArrowDown')
    p.keyboard.press('Enter')
    p.wait_for_selector('#ag97-editeur[open]')
    j = lundi + timedelta(days=1)
    verifier((p.input_value('#ag97-editeur input[name=debut]'), p.input_value('#ag97-editeur input[name=fin]')) == ('%sT08:00' % j, '%sT09:00' % j),
             'P97a2 : l’équivalent clavier du glisser ne donne pas 8 h – 9 h')
    p.keyboard.press('Escape')
    s.propre('saisie')
    # 125 %, 200 %, mobile, sombre
    for nom, vue, sombre in (('125 %', {'width': 1536, 'height': 760}, False), ('200 %', {'width': 960, 'height': 475}, False),
                             ('mobile', {'width': 375, 'height': 812}, False), ('sombre', {'width': 1920, 'height': 950}, True)):
        p.set_viewport_size(vue)
        p.emulate_media(color_scheme='dark' if sombre else 'light')
        s.aller('/planning?vue=agenda&agenda=personnel&view=week&date=' + lundi.isoformat())
        m = p.evaluate(MESURE)
        verifier(not m['defilementH'], 'P97a3 : %s : défilement horizontal' % nom)
        verifier(m['nouveau'] and m['barre'] >= 0, 'P97a3 : %s : barre ou « + Nouveau » absent' % nom)
        if getattr(banc, 'sortie', None):
            p.screenshot(path=str(Path(banc.sortie) / ('P97a-%s.png' % nom.replace(' %', 'pc'))))
        s.propre(nom)
    p.emulate_media(color_scheme='light')
    verifier(banc.modele.nombre() == 0, 'P97a : le modèle a été appelé (%d)' % banc.modele.nombre())
