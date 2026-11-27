"""5.6.108 (lot G) : parcours P108g du banc ``scripts/parcours-navigateur.py`` (vrai navigateur, données fictives, aucun réseau, aucun modèle).

- P108g-agenda (1920 × 1080) : clic sur un créneau → fenêtre préremplie (début, fin, durée 30 min, rappel par défaut, fuseau, responsable,
  récurrence lisibles ; Talk non configuré : non proposé) ; changement de durée → fin recalculée ; rappel choisi puis Enregistrer → un seul
  événement, avec ce rappel ; plage glissée (16 h – 17 h 30) → durée 1 h 30 ; clavier (flèche, Maj + flèche, Entrée) ; dates extraites à
  vérifier montrées à part, jamais dans la grille.
- P108g-defilement : amplitude 0 h – 24 h par 15 min à 1920 × 700 puis à 375 px : la page défile, la barre (+ Nouveau compris) reste en haut
  de l'écran et la ligne des jours juste dessous ; aucun défilement horizontal.
- P108g-produire et P108g-dossiers (1920 × 1080 puis 375 px) : livrables prêts d'abord, hauteur d'une ligne mesurée ; trois dossiers où l'IA
  a réellement enregistré un résultat (un livrable en échec plus récent ne compte pas), hauteur d'une ligne du tableau mesurée ; fiche :
  « Résultats enregistrés » avec le lien du résultat.
Non inscrit dans ``PARCOURS`` : l'intégrateur l'inscrit et le lance. Lancement seul : ``python scripts/parcours_p108g.py``.
"""
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sys
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
NAV = None
MESURES = {}
SANS_DEFILEMENT_H = 'document.documentElement.scrollWidth <= window.innerWidth + 1'


def _nav():
    if NAV is not None:
        return NAV
    import __main__ as nav
    return nav


def verifier(condition, message):
    _nav().verifier(condition, message)


def _sortie(banc):
    d = Path(getattr(banc, 'sortie', '.') or '.')
    d.mkdir(parents=True, exist_ok=True)
    return d


def jour_cabinet():
    return datetime.now(ZoneInfo('Europe/Paris')).date()


# ------------------------------------------------------------------------------------------------ données fictives
def semer_echeances(banc):
    from agent import echeances450
    j = jour_cabinet()
    with banc.desk() as d:
        echeances450.ensure_schema(d)
        for ident, matter, due, statut in (('p108g-ech-1', 'DOS-001', (j + timedelta(days=3)).isoformat(), 'a_confirmer'),
                                           ('p108g-ech-2', 'DOS-002', '', 'a_completer'),
                                           ('p108g-ech-3', 'DOS-003', (j + timedelta(days=2)).isoformat(), 'confirmee')):
            d.db.execute('INSERT OR REPLACE INTO deadlines450(id,matter,rule_id,start_event,start_date,due,status,origin,created,updated) VALUES(?,?,?,?,?,?,?,?,?,?)',
                         (ident, matter, 'appel_908', 'notification', j.isoformat(), due, statut, 'auto', '2026-11-18T08:00:00+00:00', '2026-11-18T08:00:00+00:00'))
        d.db.commit()


def semer_livrables(banc, n=12):
    """``n`` versions prêtes (registre des livrables), un livrable en échec plus récent pour DOS-003 ; résultats projetés comme le worker."""
    from agent import livrables5689 as liv, resultats56100
    maintenant = datetime.now(timezone.utc)
    dossiers = ('DOS-001', 'DOS-002', 'DOS-003')
    with banc.desk() as d:
        liv.ensure_schema(d)
        for i in range(n):
            quand = (maintenant - timedelta(minutes=10 + i)).isoformat()
            ident = 'p108g-%02d' % i
            d.db.execute('INSERT INTO prepared_items5689(id,document_id,version_id,version_number,matter_id,kind,title,path,sha256,source_service,source_object_id,'
                         "source_version,production_state,lifecycle,review_state,source_at,created,updated) VALUES(?,?,?,1,?,?,?,?,?,'docrequest520',?,'1',?,'current',?,?,?,?)",
                         (ident, 'doc-' + ident, 'v-' + ident, dossiers[i % 3], 'note' if i % 2 else 'conclusions', 'Projet fictif n° %d' % (i + 1), '',
                          hashlib.sha256(ident.encode()).hexdigest(), ident, 'verified', 'reviewed' if i % 2 else 'to_review', quand, quand, quand))
        quand = (maintenant - timedelta(minutes=1)).isoformat()
        d.db.execute('INSERT INTO prepared_items5689(id,document_id,version_id,version_number,matter_id,kind,title,path,sha256,source_service,source_object_id,'
                     "source_version,production_state,lifecycle,review_state,source_at,created,updated) VALUES(?,?,?,1,?,?,?,?,?,'docrequest520',?,'1','failed','current','to_review',?,?,?)",
                     ('p108g-echec', 'doc-p108g-echec', 'v-p108g-echec', 'DOS-003', 'note', 'Tentative en échec', '', '', 'p108g-echec', quand, quand, quand))
        d.db.commit()
        resultats56100.rattraper(d)


# ------------------------------------------------------------------------------------------------ parcours
def p108g_agenda(banc, s):
    import parcours_p97a
    parcours_p97a._nav = _nav
    p = s.page
    lundi = parcours_p97a.semer(banc)
    semer_echeances(banc)
    p.set_viewport_size({'width': 1920, 'height': 1080})
    s.connecter()
    s.aller('/planning?vue=agenda&agenda=personnel&view=week&date=' + lundi.isoformat())
    p.wait_for_selector('[data-ag97][data-ag97-pret]')
    # dates extraites à vérifier : à part, jamais dans la grille
    bandeau = p.locator('.ag108-verifier')
    verifier(bandeau.count() == 1 and bandeau.get_attribute('data-ag108-verifier') == '2', 'P108g : bandeau des dates à vérifier absent ou faux')
    verifier(p.locator('[data-ag97-grille]').inner_text().find('À vérifier') < 0, 'P108g : une date à vérifier est dans la grille confirmée')
    # clic sur un créneau libre : fenêtre préremplie et lisible
    mardi = lundi + timedelta(days=1)
    p.locator('.ag97-case[data-jour="%s"][data-minute="900"]' % mardi).click()
    p.wait_for_selector('#ag97-editeur[open]')
    ed = '#ag97-editeur '
    lu = p.evaluate("""() => { const f = document.querySelector('[data-ag97-form]');
        const v = (n) => f.elements[n] ? f.elements[n].value : null; const vis = (s) => { const x = f.querySelector(s); return !!(x && x.offsetParent); };
        return {debut: v('debut'), fin: v('fin'), duree: v('duree'), rappel: v('rappel'), zone: f.querySelector('[data-ag108-zone]').textContent,
                resp: f.querySelector('[data-ag108-resp-ev] dd').textContent, rec: f.querySelector('[data-ag108-recurrence]').textContent,
                talk: !!f.querySelector('input[name=visio][value=talk]'), dureeVisible: vis('select[name=duree]'), rappelVisible: vis('select[name=rappel]'),
                dossier: vis('select[name=dossier]')}; }""")
    MESURES['editeur_clic'] = lu
    verifier(lu['debut'] == '%sT15:00' % mardi and lu['fin'] == '%sT15:30' % mardi and lu['duree'] == '30', 'P108g : fenêtre mal préremplie : %s' % lu)
    verifier(lu['rappel'] == '1440,60' and lu['zone'] == 'Europe/Paris' and lu['resp'] and lu['rec'] == 'Aucune (événement unique)',
             'P108g : rappel, fuseau, responsable ou récurrence illisible : %s' % lu)
    verifier(lu['dureeVisible'] and lu['rappelVisible'] and lu['dossier'] and not lu['talk'], 'P108g : champs masqués ou Talk proposé sans configuration : %s' % lu)
    p.screenshot(path=str(_sortie(banc) / 'P108g-editeur-1920.png'))
    p.select_option(ed + 'select[name=duree]', '90')
    verifier(p.input_value(ed + 'input[name=fin]') == '%sT16:30' % mardi, 'P108g : la durée ne recalcule pas la fin')
    p.select_option(ed + 'select[name=rappel]', '30')
    p.fill(ed + 'input[name=titre]', 'Rendez-vous fictif BERNARD')
    p.select_option(ed + 'select[name=dossier]', 'DOS-002')
    with p.expect_navigation(timeout=15000):
        with p.expect_response(lambda r: '/m5689/agenda/evenements' in r.url and r.request.method == 'POST') as rep:
            p.dblclick(ed + '[data-ag97-enregistrer]')
    verifier(rep.value.status in (200, 201), 'P108g : création refusée (HTTP %d)' % rep.value.status)
    p.wait_for_selector('[data-ag97][data-ag97-pret]')
    with banc.desk() as d:
        rows = d.db.execute("SELECT reminders, matter, starts, ends FROM agenda5634 WHERE title='Rendez-vous fictif BERNARD'").fetchall()
    verifier(len(rows) == 1, 'P108g : double clic : %d événement(s)' % len(rows))
    verifier(json.loads(rows[0][0]) == [30] and rows[0][1] == 'DOS-002', 'P108g : rappel ou dossier non enregistrés : %s' % (tuple(rows[0]),))
    duree = (datetime.fromisoformat(rows[0][3]) - datetime.fromisoformat(rows[0][2])).total_seconds() / 60
    verifier(duree == 90, 'P108g : durée enregistrée %s min' % duree)
    # rouvrir l'événement : rappel réel relu
    p.locator('.ag97-ev', has_text='Rendez-vous fictif BERNARD').first.click()
    p.wait_for_selector('#ag97-editeur[open]')
    p.wait_for_function("() => document.querySelector('[data-ag97-form]').elements.titre.value === 'Rendez-vous fictif BERNARD'")
    verifier(p.input_value(ed + 'select[name=rappel]') == '30', 'P108g : le rappel enregistré n’est pas relu')
    p.keyboard.press('Escape')
    # plage glissée 16 h → 17 h 30 : durée 1 h 30
    a = p.locator('.ag97-case[data-jour="%s"][data-minute="960"]' % lundi).bounding_box()
    b = p.locator('.ag97-case[data-jour="%s"][data-minute="1020"]' % lundi).bounding_box()
    p.mouse.move(a['x'] + 6, a['y'] + 3)
    p.mouse.down()
    p.mouse.move(b['x'] + 6, b['y'] + 3, steps=8)
    p.mouse.up()
    p.wait_for_selector('#ag97-editeur[open]')
    verifier(p.input_value(ed + 'select[name=duree]') == '90', 'P108g : la plage glissée ne donne pas 1 h 30')
    p.keyboard.press('Escape')
    # clavier
    p.locator('.ag97-case[data-jour="%s"][data-minute="480"]' % lundi).focus()
    p.keyboard.press('ArrowRight')
    p.keyboard.press('Shift+ArrowDown')
    p.keyboard.press('Enter')
    p.wait_for_selector('#ag97-editeur[open]')
    verifier(p.input_value(ed + 'select[name=duree]') == '60', 'P108g : la plage au clavier ne donne pas 1 h')
    p.keyboard.press('Escape')
    s.propre('agenda : éditeur')
    verifier(banc.modele.nombre() == 0, 'P108g : le modèle a été appelé')


def p108g_defilement(banc, s):
    p = s.page
    with banc.desk() as d:
        from agent import agenda5697
        agenda5697.schema(d)
    s.connecter()
    for nom, vue in (('1920x700', {'width': 1920, 'height': 700}), ('375', {'width': 375, 'height': 812})):
        p.set_viewport_size(vue)
        s.aller('/planning?vue=agenda&agenda=personnel&view=day&plage=journee&date=' + jour_cabinet().isoformat())
        p.wait_for_selector('[data-ag97][data-ag97-pret]')
        p.evaluate('window.scrollTo(0, document.scrollingElement.scrollHeight)')
        p.wait_for_timeout(200)
        m = p.evaluate("""() => { const b = document.querySelector('.ag97-barre').getBoundingClientRect();
            const n = document.querySelector('[data-ag97-nouveau]').getBoundingClientRect(); const j = document.querySelector('.ag97-jour').getBoundingClientRect();
            return {defile: window.scrollY, barreHaut: b.top, barreBas: b.bottom, nouveau: n.top >= 0 && n.bottom <= innerHeight, jourHaut: j.top,
                    h: innerHeight, horiz: document.documentElement.scrollWidth > innerWidth + 1}; }""")
        MESURES['defilement_' + nom] = m
        verifier(m['defile'] > 0, 'P108g : %s : la page ne défile pas (amplitude 0 h – 24 h)' % nom)
        verifier(-1 <= m['barreHaut'] <= 2 and m['nouveau'], 'P108g : %s : barre ou « + Nouveau » hors de l’écran pendant le défilement : %s' % (nom, m))
        verifier(m['barreBas'] - 2 <= m['jourHaut'] <= m['barreBas'] + 4, 'P108g : %s : ligne des jours non collée sous la barre : %s' % (nom, m))
        verifier(not m['horiz'], 'P108g : %s : défilement horizontal' % nom)
        p.screenshot(path=str(_sortie(banc) / ('P108g-defilement-%s.png' % nom)))
        p.locator('[data-ag97-nouveau]').click()                                # la commande reste utilisable pendant le défilement
        p.wait_for_selector('#ag97-editeur[open]')
        p.keyboard.press('Escape')
        s.propre('défilement %s' % nom)


def p108g_produire_dossiers(banc, s):
    p = s.page
    semer_livrables(banc)
    sortie = _sortie(banc)
    s.connecter()
    for nom, vue in (('1920', {'width': 1920, 'height': 1080}), ('375', {'width': 375, 'height': 812})):
        p.set_viewport_size(vue)
        s.aller('/production')
        lignes = p.evaluate("""() => Array.from(document.querySelectorAll('#p100-documents .p100-carte')).map(x => { const r = x.getBoundingClientRect();
            return {h: Math.round(r.height), bas: Math.round(r.bottom)}; })""")
        MESURES['produire_' + nom] = {'lignes': len(lignes), 'hauteur_max': max(x['h'] for x in lignes) if lignes else 0,
                                      'visibles_sans_defiler': sum(1 for x in lignes if x['bas'] <= vue['height'])}
        verifier(len(lignes) == 10, 'P108g : Produire : %d lignes au lieu de 10 (pagination)' % len(lignes))
        avant = p.evaluate("""() => { const d = document.querySelector('#p100-documents');
            const autres = Array.from(document.querySelectorAll('#ws-main section, #ws-main .ax-card')).filter(x => x !== d && !d.contains(x) && !x.contains(d));
            return autres.every(x => d.compareDocumentPosition(x) & Node.DOCUMENT_POSITION_FOLLOWING); }""")
        verifier(avant, 'P108g : les livrables prêts ne sont pas le premier bloc de Produire')
        verifier(p.evaluate(SANS_DEFILEMENT_H), 'P108g : Produire : défilement horizontal à %s' % nom)
        if nom == '1920':
            verifier(MESURES['produire_1920']['hauteur_max'] <= 64, 'P108g : ligne de Produire trop haute : %s' % MESURES['produire_1920'])
            verifier('Tentative en échec' not in p.locator('#p100-documents').inner_text(), 'P108g : l’échec occupe la place d’un document prêt')
        s.capture(sortie / ('P108g-produire-%s.png' % nom))
        s.propre('Produire %s' % nom)
        s.aller('/dossiers')
        cartes = p.locator('.d100-carte')
        verifier(cartes.count() == 3, 'P108g : %d carte(s) « Travaillés récemment par l’IA »' % cartes.count())
        verifier('p108g-echec' not in ' '.join(p.locator('.d100-resultat').evaluate_all('l => l.map(a => a.href)')), 'P108g : la tentative en échec compte')
        rangs = p.evaluate("""() => Array.from(document.querySelectorAll('.d97-table tbody tr')).map(x => Math.round(x.getBoundingClientRect().height))""")
        MESURES['dossiers_' + nom] = {'lignes': len(rangs), 'hauteur_max': max(rangs) if rangs else 0}
        if nom == '1920':
            verifier(MESURES['dossiers_1920']['hauteur_max'] <= 52, 'P108g : ligne du tableau Dossiers trop haute : %s' % MESURES['dossiers_1920'])
        verifier(p.evaluate(SANS_DEFILEMENT_H), 'P108g : Dossiers : défilement horizontal à %s' % nom)
        s.capture(sortie / ('P108g-dossiers-%s.png' % nom))
        s.propre('Dossiers %s' % nom)
    p.set_viewport_size({'width': 1920, 'height': 1080})
    s.aller('/dossier?id=DOS-001')
    bloc = p.locator('#d79-ia')
    verifier(bloc.count() == 1 and 'Résultats enregistrés' in bloc.inner_text(), 'P108g : fiche sans « Résultats enregistrés »')
    verifier(bloc.locator('a[href*="/livrables/doc-p108g-"]').count() >= 1, 'P108g : fiche : aucun lien vers le résultat')
    # 5.6.111 (lot U) : un second bloc au même style (.u111-reprise) donne les résultats de l'IA et les pièces sources.
    verifier(p.locator('.d100-reprise:not(.u111-reprise)').count() == 1, 'P108g : en-tête de reprise absent de la fiche')
    verifier(p.locator('.u111-reprise').count() <= 1, 'P108g : bloc « Résultats de l’IA et pièces » répété')
    s.capture(sortie / 'P108g-fiche-1920.png')
    s.propre('fiche')


PARCOURS_G = (('P108g-agenda', 'Agenda : fenêtre préremplie lisible, durée, rappel, un seul événement, dates à vérifier à part', p108g_agenda),
              ('P108g-defilement', 'Agenda : barre et ligne des jours visibles pendant le défilement (0 h – 24 h, 1920 × 700 et 375 px)', p108g_defilement),
              ('P108g-produire', 'Produire et Dossiers : livrables prêts en lignes denses, trois résultats réels, fiche', p108g_produire_dossiers))


def main(argv=None):
    """Lancement seul (sans inscription dans ``PARCOURS``) : banc chargé comme module, parcours ajoutés, mesures écrites dans la sortie."""
    global NAV
    import argparse
    import importlib.util
    import tempfile
    parser = argparse.ArgumentParser()
    parser.add_argument('--sortie', default=str(Path(tempfile.gettempdir()) / 'axiorhub-parcours-p108g'))
    parser.add_argument('--navigateur', default='chromium')
    parser.add_argument('--parcours', default=','.join(i for i, _, _ in PARCOURS_G))
    args = parser.parse_args(argv)
    sys.path.insert(0, str(ROOT))
    sys.path.insert(0, str(ROOT / 'scripts'))
    spec = importlib.util.spec_from_file_location('parcours_navigateur', str(ROOT / 'scripts' / 'parcours-navigateur.py'))
    NAV = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(NAV)
    NAV.PARCOURS = NAV.PARCOURS + PARCOURS_G
    code = NAV.lancer(args.navigateur, args.sortie, tuple(x for x in args.parcours.split(',') if x))
    Path(args.sortie).mkdir(parents=True, exist_ok=True)
    (Path(args.sortie) / 'P108g-mesures.json').write_text(json.dumps(MESURES, ensure_ascii=False, indent=1), encoding='utf-8')
    print(json.dumps(MESURES, ensure_ascii=False, indent=1))
    return code


if __name__ == '__main__':
    sys.exit(main())
