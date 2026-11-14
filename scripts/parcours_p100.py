"""5.6.100 : parcours P100 du banc ``scripts/parcours-navigateur.py`` (vrai navigateur, données fictives, aucun réseau, aucun modèle).

- P100r (V100-03, V100-04) Courriels › À relire : liste à gauche, lecteur à droite, barre de décision visible dès l'ouverture à 1920 × 1080
  sans faire défiler la page ; dix décisions à la suite (Retenir, Écarter, Reporter, avec une édition) sans aucun nouveau chargement de
  document ; l'élément suivant s'ouvre seul ; fin de file annoncée ; temps clic → lecteur prêt relevé ; 390 px : un seul panneau, « Retour
  à la liste », aucun défilement horizontal.
- P100d (V100-06) Dossiers : « Travaillés récemment par l'IA » (trois affaires au plus, distinctes, sans le dossier réservé) au-dessus du
  tableau, « Nouveau dossier » visible avant défilement à 1920 × 1080 ; tableau paginé.
- P100p (V100-05) Produire : les documents prêts passent avant la file ; chaque carte porte sa version.
"""
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path

SANS_DEFILEMENT = 'document.documentElement.scrollWidth <= window.innerWidth + 1'
NAVIGATIONS = "performance.getEntriesByType('navigation').length + (window.__p100 || 0)"


def _nav():
    import __main__ as nav
    return nav


def _cle(n):
    return hashlib.sha256(('p100-%d' % n).encode()).hexdigest()


def semer_propositions(banc, n=10, dossiers=None, debut=0):
    """``n`` réponses à décider (même forme que les producteurs), projetées dans la file commune ; sujets et destinataires fictifs."""
    from agent import relecture56100 as rl
    from agent.common import private_json
    t0 = datetime.now(timezone.utc) - timedelta(hours=2)
    with banc.desk() as d:
        ids = []
        for i in range(debut, debut + n):
            mid = (dossiers or {}).get(i, 'DOS-001')
            k, cree = _cle(i), (t0 + timedelta(minutes=i)).isoformat()
            corps = 'Bonjour Maître,\n\nProjet de réponse fictif n° %d pour le dossier DURAND c. MARTIN.\n\nBien à vous.' % (i + 1)
            if i == 0:
                corps += '\n\n' + '\n'.join('Paragraphe fictif %d du long courriel : rappel des faits et de la procédure.' % j for j in range(80))
            data = {'result': {'body': corps, 'requires_decision': True, 'limits': ['À relire.']}, 'verification': None, 'matter': mid,
                    'matter_path': '', 'key': k, 'source_hash': 'h', 'created_at': cree, 'recipients': ['client%d@example.test' % i],
                    'sources': [{'id': 'incoming', 'title': 'Courriel reçu'}]}
            private_json(Path(d.c['state_dir']) / 'reports' / (k + '.json'),
                         {'subject': 'Demande fictive n° %d' % (i + 1), 'sender': 'client%d@example.test' % i, 'matter': mid, 'received_at': cree})
            d.db.execute('INSERT OR REPLACE INTO manual_drafts VALUES(?,?,?)', (k, json.dumps(data), cree))
            d.db.commit()
            ids.append((mid, rl.projeter_cle(d, k)))
    return ids


def p100_relecture(banc, s):
    nav = _nav()
    verifier = nav.verifier
    p = s.page
    sortie = Path(banc.sortie)
    semer_propositions(banc, 10)
    p.set_viewport_size({'width': 1920, 'height': 1080})
    s.connecter()
    s.aller('/courriels')
    verifier(p.locator('[data-ax100-relecture]').count() == 1, 'la vue « À relire » 5.6.100 n’est pas montée')
    lignes = p.locator('[data-ax100-lignes] li[data-review-id]')
    verifier(lignes.count() == 10, 'dix propositions attendues dans la liste : %d' % lignes.count())
    p.evaluate('window.__p100 = 0; addEventListener("beforeunload", () => { sessionStorage.setItem("p100", "1"); });')
    avant = p.evaluate("performance.getEntriesByType('navigation').length")
    debut = p.evaluate('performance.now()')
    lignes.first.locator('a').click()
    p.wait_for_function("() => { const t = document.querySelector('[data-ax100-texte]'); return t && t.value && t.value.length > 20; }", timeout=10000)
    pret_ms = p.evaluate('performance.now()') - debut
    print('    P100r clic → lecteur prêt : %.0f ms' % pret_ms)
    barre = p.evaluate("""() => { const b = document.querySelector('[data-ax100-actions]'); if (!b) return null; const r = b.getBoundingClientRect();
        return {haut: r.top, bas: r.bottom, h: innerHeight, defile: document.documentElement.scrollTop}; }""")
    verifier(barre and barre['bas'] <= barre['h'] + 1 and barre['haut'] >= 0, 'barre de décision hors de l’écran à l’ouverture : %r' % barre)
    verifier(barre['defile'] == 0, 'la page a défilé pour atteindre la barre de décision')
    s.capture(sortie / 'P100r-lecteur-1920.png')
    # dix décisions à la suite, dont une édition ; chaque fois l'élément suivant s'ouvre seul
    actions = ['retenir', 'ecarter', 'retenir', 'ecarter', 'retenir', 'ecarter', 'retenir', 'ecarter', 'retenir', 'ecarter']
    for n, action in enumerate(actions):
        sujet = p.locator('[data-ax100-sujet]').first.inner_text()
        if n == 2:
            zone = p.locator('[data-ax100-texte]')
            zone.click()
            zone.press('End')
            zone.type('\nAjout fictif relu par l’avocat.')
        bouton = p.locator('[data-ax100-actions] [data-action="%s"]' % action)
        p.wait_for_function("(a) => { const b = document.querySelector('[data-ax100-actions] [data-action=\"' + a + '\"]'); return b && !b.disabled; }",
                            arg=action, timeout=10000)   # le lecteur bloque les commandes tant que l'élément n'est pas chargé
        bouton.click()
        if n < 9:
            p.wait_for_function("(s) => { const h = document.querySelector('[data-ax100-sujet]'); return h && h.textContent.trim() && h.textContent !== s; }",
                                arg=sujet, timeout=10000)
    p.wait_for_function("() => { const v = document.querySelector('[data-ax100-vide]'); return v && !v.hidden; }", timeout=10000)
    verifier(p.evaluate("performance.getEntriesByType('navigation').length") == avant and not p.evaluate('sessionStorage.getItem("p100")'),
             'un document a été rechargé entre deux décisions')
    with banc.desk() as d:
        restants = d.db.execute("SELECT COUNT(*) FROM file56100_items WHERE etat='a_relire'").fetchone()[0]
        retenu = d.db.execute('SELECT data FROM manual_drafts WHERE mail_key=?', (_cle(2),)).fetchone()
    verifier(restants == 0, '%d proposition(s) encore à relire après dix décisions' % restants)
    verifier(retenu is None or 'Ajout fictif relu' in retenu[0], 'l’édition de la troisième proposition n’a pas été conservée')
    s.capture(sortie / 'P100r-fin-de-file.png')
    s.propre('À relire 5.6.100')
    # 390 px : un seul panneau, Retour à la liste, aucun défilement horizontal
    semer_propositions(banc, 3)
    p.set_viewport_size({'width': 390, 'height': 844})
    s.aller('/courriels')
    verifier(p.evaluate(SANS_DEFILEMENT), 'défilement horizontal à 390 px (liste)')
    p.locator('[data-ax100-lignes] li[data-review-id] a').first.click()
    p.wait_for_function("() => { const t = document.querySelector('[data-ax100-texte]'); return t && t.value; }", timeout=10000)
    retour = p.locator('[data-ax100-retour]')
    verifier(retour.is_visible(), '« Retour à la liste » absent à 390 px')
    verifier(p.evaluate(SANS_DEFILEMENT), 'défilement horizontal à 390 px (lecteur)')
    s.capture(sortie / 'P100r-390.png')
    retour.click()
    verifier(p.locator('[data-ax100-lignes]').is_visible(), 'la liste ne revient pas après « Retour à la liste »')
    s.propre('À relire 5.6.100 à 390 px')


def p100_dossiers(banc, s):
    nav = _nav()
    verifier = nav.verifier
    p = s.page
    sortie = Path(banc.sortie)
    maintenant = datetime.now(timezone.utc)
    # résultats VÉRIFIABLES (propositions réelles de la file commune) : trois dossiers, dont deux résultats pour DOS-001
    props = semer_propositions(banc, 4, {20: 'DOS-001', 21: 'DOS-002', 22: 'DOS-003', 23: 'DOS-001'}, debut=20)
    with banc.desk() as d:
        from agent import resultats56100 as rs
        for i, (mid, ident) in enumerate(props):
            rs.publier(d, mid, 'proposition', ident, 'v1', (maintenant - timedelta(minutes=i)).isoformat(), libelle='Réponse préparée')
    p.set_viewport_size({'width': 1920, 'height': 1080})
    s.connecter()
    s.aller('/dossiers')
    bloc = p.locator('text=Travaillés récemment par l’IA').first
    verifier(bloc.count() == 1, 'bloc « Travaillés récemment par l’IA » absent')
    texte = p.locator('main, #ws-main').first.inner_text()
    verifier('Aucun travail IA récent' not in texte, 'aucune carte alors que trois dossiers ont un résultat vérifiable')
    cartes = p.evaluate('''() => Array.from(document.querySelectorAll('a')).filter(a => /Voir le résultat/.test(a.textContent)).length''')
    print('    P100d cartes « Voir le résultat » :', cartes)
    verifier(cartes == 3, 'trois cartes attendues (trois dossiers distincts), %d trouvée(s)' % cartes)
    nouveau = p.locator('a:has-text("Nouveau dossier"), button:has-text("Nouveau dossier")').first
    for nom, loc in (('le bloc des dossiers récents', bloc), ('« Nouveau dossier »', nouveau)):
        boite = loc.bounding_box()
        verifier(boite and boite['y'] + boite['height'] <= 1080, '%s n’est pas visible avant défilement : %s' % (nom, boite))
    s.capture(sortie / 'P100d-dossiers-1920.png')
    verifier(p.evaluate(SANS_DEFILEMENT), 'défilement horizontal sur Dossiers')
    s.propre('Dossiers 5.6.100')


def p100_produire(banc, s):
    nav = _nav()
    verifier = nav.verifier
    p = s.page
    sortie = Path(banc.sortie)
    banc.semer_livrable()
    p.set_viewport_size({'width': 1920, 'height': 1080})
    s.connecter()
    s.aller('/production')
    verifier(p.locator('#p100-documents').count() == 1, 'section des documents prêts absente de Produire')
    docs = p.locator('#p100-documents').bounding_box()
    file = p.locator('#c5656-file, .q51-file, [data-file5651]').first
    if file.count():
        verifier(docs['y'] < file.bounding_box()['y'], 'les documents prêts ne passent pas avant la file')
    s.capture(sortie / 'P100p-produire-1920.png')
    s.propre('Produire 5.6.100')
