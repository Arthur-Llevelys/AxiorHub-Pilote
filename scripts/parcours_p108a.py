"""5.6.108 (lot A) : parcours P108a du banc ``scripts/parcours-navigateur.py`` (vrai navigateur, version serveur du banc, données fictives,
aucun réseau, aucun modèle réel : le faux fournisseur du banc répond).

P108a-aujourdhui :
- 1920 × 1080 : « Actuellement » en tête, puis « À décider / À relire » et « Ma journée » côte à côte ; les trois premières décisions (dossier,
  objet, raison, action), le premier rendez-vous du jour et les commandes de l'en-tête visibles sans défiler ; le nom du dossier lisible
  (14 px au moins, contraste 4,5:1 au moins, clair et sombre) ; l'heure de Ma journée ne recouvre pas le titre ; une seule commande
  « Rechercher dans le cabinet » (celle de la barre du haut) ;
- « Actualiser » pendant qu'une source ne répond pas (réponse du serveur interceptée : rubrique « Réponses à décider » en panne) : le nombre
  connu reste affiché avec « Dernière valeur connue à … » et la reprise ; la source revenue, la valeur fraîche remplace la valeur gardée ;
- 1093 × 614 (1366 × 768 à 125 %) et 375 × 812 : une colonne, aucun défilement horizontal, chaque commande des lignes atteignable.
P108a-relecture :
- 1093 × 614 : Retenir, Écarter et Reporter visibles pendant la lecture d'un long projet (texte défilé jusqu'au bout) ;
- filtre « Propositions » : après « Retenir » accepté par le serveur, le projet suivant de la même liste filtrée s'ouvre, le filtre reste ;
- 390 px : page défilée, élément ouvert, « Retour à la liste » : défilement, sélection (focus) et filtre restitués.
P108a-assistant :
- 1920 × 1080 : volet en trois zones ; aides dépliées (contexte, exemples, options) : la saisie et Envoyer restent visibles ; Échap rend le
  focus au bouton d'origine ; le brouillon revient à la réouverture et sur une autre page ; un refus du serveur garde le brouillon ;
- reçu : « Reçu n° » égal au début de l'identifiant de la mission enregistrée, étape « Reçue / En attente / En cours » puis « Résultat » ou
  « Échec » après le travail ; « Retrouver dans Missions » désigne la même carte ; réponse perdue après la validation : aucune carte
  numérotée, reprise sans doublon ;
- 375 × 812 puis 375 × 420 (clavier ouvert) : Envoyer visible et non recouvert, options dépliées comprises.
Aucune erreur JavaScript ni CSP, aucune requête vers une autre adresse que le banc. Non inscrit dans ``PARCOURS`` : l'intégrateur l'inscrit.
Lancement seul : ``python scripts/parcours_p108a.py [--parcours P108a-aujourdhui,P108a-relecture,P108a-assistant] [--sortie DOSSIER]``.
"""
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parent.parent
NAV = None

SANS_DEFILEMENT = 'document.documentElement.scrollWidth <= window.innerWidth + 1'
# élément visible dans la zone visuelle et non recouvert en son centre ('' si oui, sinon la raison)
VISIBLE = """(sel) => { const n = Array.from(document.querySelectorAll(sel)).find(x => x.getClientRects().length && x.getBoundingClientRect().width > 0);
  if (!n) return 'absent'; const r = n.getBoundingClientRect(), vv = window.visualViewport || {height: innerHeight, offsetTop: 0, width: innerWidth};
  if (r.top < vv.offsetTop - 1 || r.bottom > vv.offsetTop + vv.height + 1 || r.left < -1 || r.right > vv.width + 1)
    return 'hors de la fenêtre (' + Math.round(r.top) + '-' + Math.round(r.bottom) + ' / ' + Math.round(vv.height) + ')';
  const e = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
  return e && (e === n || n.contains(e) || e.contains(n)) ? '' : 'recouvert par ' + (e ? e.tagName + '.' + e.className : 'rien'); }"""
# atteignable : de taille non nulle et dans la largeur de la page (on peut y défiler verticalement)
ATTEIGNABLE = """(sel) => Array.from(document.querySelectorAll(sel)).filter(x => x.getClientRects().length).map(x => { const r = x.getBoundingClientRect();
  return r.width >= 1 && r.height >= 1 && r.left >= -1 && r.right <= document.documentElement.clientWidth + 1 ? '' : (x.textContent.trim().slice(0, 40) || x.tagName); }).filter(Boolean)"""
CONTRASTE = """(sel) => { const n = document.querySelector(sel); if (!n) return null;
  const rgb = c => (c.match(/[\\d.]+/g) || [0, 0, 0, 0]).map(Number);
  const lum = c => { const v = c.slice(0, 3).map(x => { x /= 255; return x <= 0.03928 ? x / 12.92 : Math.pow((x + 0.055) / 1.055, 2.4); }); return 0.2126 * v[0] + 0.7152 * v[1] + 0.0722 * v[2]; };
  let fond = [255, 255, 255, 1];
  for (let x = n; x; x = x.parentElement) { const c = rgb(getComputedStyle(x).backgroundColor); if (c.length < 4 || c[3] > 0.5) { fond = c; break; } }
  const s = getComputedStyle(n), a = lum(rgb(s.color)), b = lum(fond);
  return {taille: parseFloat(s.fontSize), contraste: (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05)}; }"""


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


def semer(banc, propositions=12):
    from datetime import datetime, timedelta, timezone
    nav = _nav()
    from parcours_p100 import semer_propositions
    from parcours_p101r import semer_note
    nav._accueil96(banc, meteo=False)                 # échéance à vérifier, échéance confirmée du jour, document Word préparé, prénom
    semer_propositions(banc, propositions)            # la première est longue (quatre-vingts paragraphes)
    semer_note(banc)
    semis = nav._semis5695()
    maintenant = datetime.now(timezone.utc)
    with banc.desk() as d:
        semis.evenement(d, 'banc-ev108', 'Audience fictive de mise en état', maintenant + timedelta(minutes=50), matter='DOS-001')
        semis.evenement(d, 'banc-ev108b', 'Rendez-vous fictif ZORVAL', maintenant + timedelta(minutes=90), matter='DOS-003')
        d.db.commit()


# ------------------------------------------------------------------------------------------------ Aujourd'hui
def p108a_aujourdhui(banc, s):
    p = s.page
    sortie = Path(banc.sortie)
    semer(banc)
    p.set_viewport_size({'width': 1920, 'height': 1080})
    s.connecter()
    s.aller('/aujourdhui')
    verifier(p.locator('.c530.f100').count() == 1, 'nouvelle présentation d’Aujourd’hui absente')
    ys = [p.locator(sel).first.bounding_box()['y'] for sel in ('#ax95-actuellement', '.f96-rang')]
    verifier(ys == sorted(ys), '« Actuellement » n’est pas en tête : %s' % ys)
    decider, journee = p.locator('#f96-decider').bounding_box(), p.locator('.f96-journee').bounding_box()
    verifier(journee['x'] >= decider['x'] + decider['width'] - 1 and journee['y'] < decider['y'] + decider['height'],
             '« À décider » et « Ma journée » ne sont pas côte à côte à 1920 px : %s / %s' % (decider, journee))
    lignes = p.locator('#f96-lignes-zone li.f96-ligne')
    verifier(lignes.count() == 3, 'trois premières décisions attendues : %d' % lignes.count())
    for i in range(3):
        li = lignes.nth(i)
        for part in ('.f96-ligne-dossier', '.f96-ligne-objet', '.f96-ligne-raison', '.f96-ligne-bouton'):
            verifier(li.locator(part).inner_text().strip(), 'ligne %d : %s vide' % (i + 1, part))
        _visible(p, '#f96-lignes-zone li.f96-ligne:nth-child(%d) .f96-ligne-bouton' % (i + 1), 'décision %d sans défiler (1920 × 1080)' % (i + 1))
    boutons = p.eval_on_selector_all('#f96-lignes-zone .f96-ligne-bouton', 'l => l.map(a => a.textContent.trim())')
    verifier(all(b in ('Relire la réponse', 'Vérifier la date', 'Ouvrir le document', 'Voir l’échéance', 'Voir l’audience', 'Ouvrir') for b in boutons),
             'libellés d’action inattendus : %s' % boutons)
    _visible(p, '.f100-agenda .f100-ev a', 'premier rendez-vous du jour sans défiler')
    for sel in ('#a92-pause', '.a89-mission'):
        _visible(p, sel, 'commande de l’en-tête')
    dossier = p.evaluate(CONTRASTE, '#f96-lignes-zone .f96-ligne-dossier')
    verifier(dossier['taille'] >= 14 and dossier['contraste'] >= 4.5, 'nom du dossier peu lisible : %s' % dossier)
    heure = p.evaluate("""() => Array.from(document.querySelectorAll('.f100-agenda .f100-ev')).map(li => {
        const t = li.querySelector('time').getBoundingClientRect(), a = li.querySelector('a').getBoundingClientRect();
        return t.right <= a.left + 1 || t.bottom <= a.top + 1; })""")
    verifier(heure and all(heure), 'Ma journée : l’heure recouvre le titre : %s' % heure)
    recherches = p.evaluate("""() => Array.from(document.querySelectorAll('a[data-m82-quick]')).filter(a => a.getClientRects().length).length""")
    verifier(recherches == 1, '« Rechercher dans le cabinet » répété (%d commandes visibles)' % recherches)
    s.capture(sortie / 'P108a-aujourdhui-1920x1080.png')
    p.emulate_media(color_scheme='dark')
    sombre = p.evaluate(CONTRASTE, '#f96-lignes-zone .f96-ligne-dossier')
    p.emulate_media(color_scheme='light')
    verifier(sombre['contraste'] >= 4.5, 'nom du dossier peu lisible en mode sombre : %s' % sombre)
    s.propre('Aujourd’hui à 1920 × 1080')

    # « Actualiser » pendant une panne de la source des réponses : la dernière valeur connue reste, avec la reprise
    avant = p.locator('#ax95-zone li[data-rubrique="reponses"] .ax95-nombre').inner_text().strip()
    verifier(re.fullmatch(r'[1-9]\d*', avant), 'nombre de réponses à décider inattendu : %r' % avant)
    motif = re.compile(r'(<li data-rubrique="reponses" data-etat=")[^"]*("(?: data-zero="1")?>.*?</li>)', re.S)
    vus = []

    def remplacer(m):
        li = m.group(1) + 'panne' + m.group(2).replace(' data-zero="1"', '')
        return re.sub(r'(<span class="ax95-nombre"[^>]*>)[^<]*(</span>)', '\\g<1>–\\g<2>', li)

    def panne(route):
        entetes = {k: x for k, x in route.request.headers.items() if k.lower() != 'if-none-match'}   # réponse complète, jamais 304
        v = route.fetch(headers=entetes).json()
        vus.append(bool(motif.search(v.get('html') or '')))
        v['html'] = motif.sub(remplacer, v.get('html') or '')
        v['etag'] = 'banc-panne'
        route.fulfill(status=200, content_type='application/json', body=json.dumps(v))
    p.route('**/api440/m5689/actuellement/etat', panne)
    with p.expect_response(lambda r: '/actuellement/etat' in r.url):
        p.click('#ax95-actuellement [data-ax95="actualiser"]')
    p.wait_for_timeout(300)
    verifier(vus and vus[0], 'rubrique des réponses absente de la réponse du serveur')
    ligne = p.locator('#ax95-zone li[data-rubrique="reponses"]')
    verifier(ligne.locator('.ax95-nombre').inner_text().strip() == avant, 'la valeur connue a été remplacée pendant la panne : %r' % ligne.inner_text())
    texte = ligne.inner_text()
    verifier('Dernière valeur connue à' in texte and 'source indisponible' in texte and 'nouvel essai' in texte, 'panne non dite dans la zone : %r' % texte)
    p.locator('#ax95-actuellement').screenshot(path=str(sortie / 'P108a-actuellement-panne.png'))
    p.unroute('**/api440/m5689/actuellement/etat')
    with p.expect_response(lambda r: '/actuellement/etat' in r.url):
        p.click('#ax95-actuellement [data-ax95="actualiser"]')
    p.wait_for_timeout(300)
    verifier(ligne.get_attribute('data-ax108-garde') is None and ligne.locator('.ax95-nombre').inner_text().strip() == avant,
             'la valeur fraîche ne remplace pas la valeur gardée')
    s.propre('Actuellement pendant une panne')

    for vue, nom, fichier in (({'width': 1093, 'height': 614}, '1366 × 768 à 125 %', '1093x614'), ({'width': 375, 'height': 812}, '375 px', '375')):
        p.set_viewport_size(vue)
        s.aller('/aujourdhui')
        verifier(p.evaluate(SANS_DEFILEMENT), 'défilement horizontal (%s)' % nom)
        decider, journee = p.locator('#f96-decider').bounding_box(), p.locator('.f96-journee').bounding_box()
        verifier(journee['y'] >= decider['y'] + decider['height'] - 1, '« Ma journée » encore à côté de « À décider » (%s)' % nom)
        hors = p.evaluate(ATTEIGNABLE, '#f96-lignes-zone .f96-ligne-bouton, #a92-pause, .a89-mission, .f100-agenda a, #ax95-actuellement .ax95-btn')
        verifier(not hors, 'commandes hors d’atteinte (%s) : %s' % (nom, hors))
        p.locator('#f96-lignes-zone .f96-ligne-bouton').first.scroll_into_view_if_needed()
        _visible(p, '#f96-lignes-zone .f96-ligne-bouton', 'première décision (%s)' % nom)
        s.capture(sortie / ('P108a-aujourdhui-%s.png' % fichier))
        s.propre('Aujourd’hui (%s)' % nom)


# ------------------------------------------------------------------------------------------------ À relire
def _ouvert(p):
    p.wait_for_function("() => { const t = document.querySelector('[data-ax100-texte]'); return t && t.value && t.value.length > 20; }", timeout=10000)


def p108a_relecture(banc, s):
    p = s.page
    sortie = Path(banc.sortie)
    semer(banc, 12)
    p.set_viewport_size({'width': 1093, 'height': 614})
    s.connecter()
    s.aller('/a-relire?source=proposition')
    lignes = p.locator('[data-ax100-lignes] li[data-review-id]')
    verifier(lignes.count() >= 10, 'propositions attendues dans la liste : %d' % lignes.count())
    # le long projet (« Demande fictive n° 1 », le plus ancien) : barre de décision visible au bout du texte
    p.locator('[data-ax100-lignes] li[data-review-id] a').filter(has=p.locator('strong', has_text=re.compile(r'^Demande fictive n° 1$'))).first.click()
    _ouvert(p)
    verifier(len(p.input_value('[data-ax100-texte]')) > 3000, 'le long projet ne s’est pas ouvert')
    hauteur = p.evaluate("() => document.querySelector('[data-ax100-texte]').getBoundingClientRect().height")
    verifier(hauteur >= 120, 'texte du projet écrasé à 1093 × 614 : %.0f px' % hauteur)
    p.evaluate("() => { const t = document.querySelector('[data-ax100-texte]'); t.scrollTop = t.scrollHeight; const c = t.closest('.ax100-corps'); if (c) c.scrollTop = c.scrollHeight; }")
    for action in ('retenir', 'ecarter', 'reporter'):
        _visible(p, '[data-ax100-actions] [data-action="%s"]' % action, 'pendant la lecture d’un long projet (1093 × 614)')
    s.capture(sortie / 'P108a-relecture-1093x614.png')
    p.locator('[data-ax100-retour]').click()
    # après « Retenir » accepté par le serveur : le projet suivant de la même liste filtrée s'ouvre, le filtre reste
    ids = p.eval_on_selector_all('[data-ax100-lignes] li[data-review-id]', 'l => l.map(x => x.getAttribute("data-review-id"))')
    lignes.nth(1).locator('a').click()
    _ouvert(p)
    p.wait_for_function("() => { const b = document.querySelector('[data-ax100-actions] [data-action=\"retenir\"]'); return b && !b.disabled; }", timeout=10000)
    with p.expect_response(lambda r: '/m56100/relecture/' in r.url and r.request.method == 'POST') as rep:
        p.locator('[data-ax100-actions] [data-action="retenir"]').click()
    verifier(rep.value.ok, 'décision refusée : HTTP %d' % rep.value.status)
    p.wait_for_function("(id) => new URLSearchParams(location.search).get('item') === id", arg=ids[2], timeout=10000)
    _ouvert(p)
    verifier(p.locator('[data-ax100-filtre="proposition"][aria-current="page"]').count() == 1 and 'source=proposition' in p.url,
             'le filtre « Propositions » n’est pas resté après la décision : %s' % p.url)
    verifier(p.locator('[data-ax100-lignes] li[data-review-id="%s"]' % ids[1]).count() == 0, 'la ligne décidée est restée dans la liste')
    s.propre('À relire : décision puis suivant')
    # 390 px : page défilée, ouverture, retour : défilement, sélection et filtre restitués
    p.set_viewport_size({'width': 390, 'height': 844})
    s.aller('/a-relire?source=proposition')
    p.evaluate('window.scrollTo(0, 600)')
    p.wait_for_timeout(150)
    avant = p.evaluate('window.scrollY')
    verifier(avant > 100, 'la liste ne défile pas à 390 px (%s)' % avant)
    cible = p.locator('[data-ax100-lignes] li[data-review-id] a').nth(6)
    ident = cible.get_attribute('data-review-id')
    cible.click()
    _ouvert(p)
    p.locator('[data-ax100-retour]').click()
    p.wait_for_timeout(300)
    apres = p.evaluate('window.scrollY')
    verifier(abs(apres - avant) <= 2, 'défilement non restitué à 390 px : %s puis %s' % (avant, apres))
    focus = p.evaluate('document.activeElement && document.activeElement.getAttribute("data-review-id")')
    verifier(focus == ident, 'sélection non restituée : focus sur %r au lieu de %r' % (focus, ident))
    verifier(p.locator('[data-ax100-filtre="proposition"][aria-current="page"]').count() == 1, 'filtre perdu à la fermeture')
    verifier(p.evaluate(SANS_DEFILEMENT), 'défilement horizontal à 390 px')
    s.capture(sortie / 'P108a-relecture-390-retour.png')
    s.propre('À relire à 390 px')


# ------------------------------------------------------------------------------------------------ volet de l'assistant
def _missions(banc):
    with banc.desk() as d:
        try:
            return [dict(r) for r in d.db.execute('SELECT id,request_key,instruction,state FROM missions_v567 ORDER BY created, rowid')]
        except Exception:   # noqa: BLE001 — table pas encore créée
            return []


def p108a_assistant(banc, s):
    p = s.page
    sortie = Path(banc.sortie)
    banc.modele.reponse = lambda corps: json.dumps({'answer': 'Réponse fictive du banc.', 'source_ids': [], 'limits': [], 'proposed_actions': []})
    p.set_viewport_size({'width': 1920, 'height': 1080})
    s.connecter()
    p.route('**/api440/m5680/passages', lambda route: route.fulfill(status=200, content_type='application/json', body='{"passages": []}'))
    s.aller('/aujourdhui')
    p.focus('#ws-ai-launcher')
    p.keyboard.press('Enter')
    p.wait_for_selector('#ws-ai-dock:not([hidden])')
    zones = p.evaluate("""() => { const d = document.getElementById('ws-ai-dock'), r = x => d.querySelector(x).getBoundingClientRect();
        return {tete: r('.ws-ai-tete').bottom, fil: [r('.ws-ai-body').top, r('.ws-ai-body').bottom], saisie: r('.ws-ai-composeur').top,
                defile: getComputedStyle(d.querySelector('.ws-ai-body')).overflowY}; }""")
    verifier(zones['tete'] <= zones['fil'][0] + 1 and zones['fil'][1] <= zones['saisie'] + 1 and zones['defile'] in ('auto', 'scroll'),
             'volet sans ses trois zones : %s' % zones)
    # aides dépliées : la saisie et Envoyer restent visibles
    for pli in ('.ws-ai-contexte > summary', '.ws-ai-exemples > summary', '.ws-ai-options > summary'):
        p.click('#ws-ai-dock ' + pli)
    for sel in ('#ws-ai-question', '#ws-ai-send'):
        _visible(p, sel, 'aides dépliées (1920 × 1080)')
    p.locator('#ws-ai-dock').screenshot(path=str(sortie / 'P108a-assistant-aides-1920.png'))
    p.click('#ws-ai-dock .ws-ai-options > summary')
    texte = 'Instruction fictive : résume le dossier DURAND en trois points.'
    p.fill('#ws-ai-question', texte)
    p.keyboard.press('Escape')
    p.wait_for_selector('#ws-ai-dock[hidden]', state='attached')
    verifier(p.evaluate('document.activeElement && document.activeElement.id') == 'ws-ai-launcher', 'focus non rendu au bouton d’origine')
    p.click('#ws-ai-launcher')
    verifier(p.input_value('#ws-ai-question') == texte, 'brouillon perdu à la réouverture')
    s.aller('/a-relire')
    p.click('#ws-ai-launcher')
    verifier(p.input_value('#ws-ai-question') == texte, 'brouillon perdu en changeant de page')
    # refus du serveur : le brouillon est gardé (changement de page compris), aucune carte numérotée
    p.route('**/api440/m567/mission/create', lambda route: route.fulfill(status=400, content_type='application/json',
                                                                         body=json.dumps({'error': 'refus_banc', 'message': 'Refus fictif du banc.'})))
    p.click('#ws-ai-send')
    p.wait_for_selector('#ws-ai-current .mission567-card[data-etat="refusee"]')
    carte = p.locator('#ws-ai-current .mission567-card').first
    verifier(carte.locator('.ax108-recu-numero').count() == 0 and 'Aucun reçu' in carte.inner_text(), 'refus présenté avec un reçu : %r' % carte.inner_text())
    p.unroute('**/api440/m567/mission/create')
    s.erreurs.clear()                                   # la console signale le 400 voulu
    s.aller('/aujourdhui')
    p.click('#ws-ai-launcher')
    verifier(p.input_value('#ws-ai-question') == texte, 'brouillon perdu après un refus du serveur')
    # reçu : numéro de la mission enregistrée, étape écrite, puis résultat après le travail
    with p.expect_response(lambda r: '/m567/mission/create' in r.url) as rep:
        p.click('#ws-ai-send')
    verifier(rep.value.ok, 'demande refusée : HTTP %d' % rep.value.status)
    m = _missions(banc)[-1]
    carte = p.locator('#ws-ai-current .mission567-card[data-mission-id="%s"]' % m['id'])
    carte.wait_for(timeout=10000)
    numero = carte.locator('.ax108-recu-numero').inner_text()
    attendu = 'Reçu n° %s-%s' % (m['id'][:4].upper(), m['id'][4:8].upper())
    verifier(numero == attendu, 'numéro de reçu %r au lieu de %r' % (numero, attendu))
    lisible = p.evaluate(CONTRASTE, '#ws-ai-current .mission567-card[data-mission-id="%s"] .ax108-recu-numero' % m['id'])
    verifier(lisible['contraste'] >= 4.5, 'numéro de reçu peu lisible : %s' % lisible)
    etape = carte.locator('.ax108-etapes [aria-current="step"]').inner_text()
    verifier(etape in ('Reçue', 'En attente', 'En cours'), 'étape initiale inattendue : %r' % etape)
    verifier(p.input_value('#ws-ai-question') == '', 'champ non vidé au reçu')
    restes = p.evaluate("() => Object.keys(sessionStorage).filter(k => k.startsWith('ax96-brouillon:') && sessionStorage.getItem(k))")
    verifier(not restes, 'brouillon encore gardé après le reçu : %s' % restes)
    p.locator('#ws-ai-dock').screenshot(path=str(sortie / 'P108a-assistant-recu.png'))
    verifier(banc.travailler() >= 1, 'le travail n’a pas été exécuté')
    p.evaluate('document.dispatchEvent(new Event("axiorhub:activity"))')
    p.wait_for_function("(id) => { const c = document.querySelector('#ws-ai-current .mission567-card[data-mission-id=\"' + id + '\"] .ax108-etapes [aria-current=\"step\"]');"
                        " return c && /Résultat|Échec/.test(c.textContent); }", arg=m['id'], timeout=15000)
    final = carte.locator('.ax108-etapes [aria-current="step"]').inner_text()
    print('    P108a %s : %s puis %s (état %s)' % (numero, etape, final, _missions(banc)[-1]['state']))
    lien = carte.locator('a.ax108-recu-lien').get_attribute('href')
    verifier(lien.endswith('/missions#mission-' + m['id']), 'lien « Retrouver dans Missions » inattendu : %s' % lien)
    p.goto(banc.origine + lien, wait_until='load')
    p.wait_for_selector('#mission567-list .mission567-card.ax108-cible')
    verifier(p.locator('#mission567-list .mission567-card.ax108-cible').get_attribute('data-mission-id') == m['id'], 'Missions ne désigne pas la même mission')
    verifier(p.locator('#mission567-list .ax108-cible .ax108-recu-numero').inner_text() == attendu, 'Missions : autre numéro de reçu')
    s.capture(sortie / 'P108a-missions-recu.png')
    s.propre('reçu et Missions')
    # réponse perdue après la validation : aucune carte numérotée, reprise sans doublon
    s.aller('/aujourdhui')
    p.click('#ws-ai-launcher')

    def perdue(route):
        route.fetch()
        route.abort()
    n = len(_missions(banc))
    p.route('**/api440/m567/mission/create', perdue)
    p.fill('#ws-ai-question', 'Instruction fictive : liste les pièces du dossier DURAND.')
    p.click('#ws-ai-send')
    p.wait_for_selector('#ws-ai-current .mission567-card[data-etat="incertaine"]')
    incertaine = p.locator('#ws-ai-current .mission567-card').first
    verifier(incertaine.locator('.ax108-recu-numero').count() == 0 and 'Aucun reçu du serveur' in incertaine.inner_text(), 'succès affiché sans reçu')
    p.unroute('**/api440/m567/mission/create')
    incertaine.locator('[data-ai-reprendre]').click()
    recue = _missions(banc)[-1]
    p.locator('#ws-ai-current .mission567-card[data-mission-id="%s"] .ax108-recu-numero' % recue['id']).wait_for(timeout=10000)
    verifier(len(_missions(banc)) == n + 1, 'doublon au nouvel essai : %d missions au lieu de %d' % (len(_missions(banc)), n + 1))
    s.erreurs.clear()                                   # coupure voulue signalée par la console
    s.propre('réponse perdue puis reprise')
    # téléphone, puis clavier ouvert : Envoyer visible et non recouvert, options dépliées comprises
    p.set_viewport_size({'width': 375, 'height': 812})
    p.wait_for_timeout(200)
    p.fill('#ws-ai-question', 'Instruction fictive au téléphone.')
    _visible(p, '#ws-ai-send', 'téléphone 375 × 812')
    p.set_viewport_size({'width': 375, 'height': 420})
    p.focus('#ws-ai-question')
    p.wait_for_timeout(300)
    _visible(p, '#ws-ai-send', 'clavier ouvert (375 × 420)')
    _visible(p, '#ws-ai-question', 'clavier ouvert (375 × 420)')
    p.screenshot(path=str(sortie / 'P108a-assistant-375x420.png'))
    if p.locator('#ws-ai-dock .ws-ai-options > summary').is_visible():
        p.click('#ws-ai-dock .ws-ai-options > summary')
        _visible(p, '#ws-ai-send', 'clavier ouvert, options dépliées')
    verifier(p.evaluate(SANS_DEFILEMENT), 'défilement horizontal du volet à 375 px')
    s.propre('volet au téléphone')


PARCOURS = (('P108a-aujourdhui', 'Aujourd’hui : Actuellement, décisions et Ma journée côte à côte, dernière valeur connue, 125 %, mobile', p108a_aujourdhui),
            ('P108a-relecture', 'À relire : décisions visibles en lisant, suivant dans la liste filtrée, retour restitué à 390 px', p108a_relecture),
            ('P108a-assistant', 'Assistant : trois zones, brouillon gardé, focus rendu, reçu et étape, aucun doublon, clavier ouvert', p108a_assistant))


def main(argv=None):
    """Lancement seul (sans inscription dans ``PARCOURS`` du banc)."""
    global NAV
    import argparse
    import importlib.util
    import tempfile
    parser = argparse.ArgumentParser()
    parser.add_argument('--sortie', default=str(Path(tempfile.gettempdir()) / 'axiorhub-parcours-p108a'))
    parser.add_argument('--navigateur', default='chromium')
    parser.add_argument('--parcours', default=','.join(i for i, _, _ in PARCOURS))
    args = parser.parse_args(argv)
    sys.path.insert(0, str(ROOT))
    sys.path.insert(0, str(ROOT / 'scripts'))
    spec = importlib.util.spec_from_file_location('parcours_navigateur', str(ROOT / 'scripts' / 'parcours-navigateur.py'))
    NAV = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(NAV)
    NAV.PARCOURS = NAV.PARCOURS + PARCOURS
    return NAV.lancer(args.navigateur, args.sortie, tuple(x for x in args.parcours.split(',') if x), continuer=True)


if __name__ == '__main__':
    sys.exit(main())
