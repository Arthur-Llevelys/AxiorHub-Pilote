"""5.6.101 : parcours P101r du banc ``scripts/parcours-navigateur.py`` (vrai navigateur, données fictives, aucun réseau, aucun modèle).

- P101r (lot R, R01-R05, V101-09) Courriels › À relire : liste mixte (propositions, document produit, note de l'IA), filtres par type
  mémorisés pour la personne (retrouvés après rechargement), contrat lecteur → mesure (``ax101:ouvert`` / ``ax101:decide`` sans identifiant
  ni texte), correction d'un document par l'IA sans écrasement (édition humaine concurrente : la proposition n'est pas appliquée, le fichier
  humain reste), note validée sans rechargement, 390 px sans défilement horizontal.

Enregistrement (intégrateur) : ``parcours-navigateur.py`` et ``test_v5694_parcours`` ; ce module n'y est pas inscrit par le lot R.
"""
from datetime import datetime, timezone
import hashlib

SANS_DEFILEMENT = 'document.documentElement.scrollWidth <= window.innerWidth + 1'
ECOUTE = """() => { window.__p101r = []; ['ax101:ouvert', 'ax101:decide'].forEach(n => document.addEventListener(n, ev => {
    window.__p101r.push([n, JSON.stringify(ev.detail)]); })); }"""


def _nav():
    import __main__ as nav
    return nav


def semer_note(banc, dossier='DOS-001'):
    """Une note « nouvelles écritures » à relire (forme de ``delta56100``), projetée dans la file commune."""
    from agent import relecture56101 as r1
    maintenant = datetime.now(timezone.utc).isoformat()
    rid = 'dl-' + hashlib.sha256(maintenant.encode()).hexdigest()[:24]
    with banc.desk() as d:
        d.db.execute('INSERT INTO delta56100_resultats(id,cabinet,event_id,dossier,piece_id,version_id,sha256,baseline_id,partie,titre,etat,note,created,updated) '
                     'VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)', (rid, 'cabinet', 'ev-' + rid, dossier, 'piece-p101r', 'v-p101r', 'b' * 64, 'base-1', 'défendeur',
                                                           'Conclusions fictives n° 2', 'a_relire',
                                                           'Note interne — nouvelles écritures reçues (à relire, rien n’a été appliqué au dossier)\n\nCe qui change : prix contesté.',
                                                           maintenant, maintenant))
        d.db.commit()
        r1.projeter_note(d, rid)
    return r1.id_note(rid), rid


def semer_document(banc):
    from agent import relecture56101 as r1
    doc = banc.semer_livrable()
    with banc.desk() as d:
        r1.projeter_document(d, doc['document_id'])
    return r1.id_document(doc['document_id']), doc


def p101r_relecture(banc, s):
    from parcours_p100 import semer_propositions
    nav = _nav()
    verifier = nav.verifier
    p = s.page
    sortie = __import__('pathlib').Path(banc.sortie)
    semer_propositions(banc, 3, debut=40)
    doc_id, doc = semer_document(banc)
    note_id, rid = semer_note(banc)
    p.set_viewport_size({'width': 1920, 'height': 1080})
    s.connecter()
    s.aller('/courriels?source=tous')
    p.evaluate(ECOUTE)
    types = p.evaluate("() => Array.from(document.querySelectorAll('[data-ax100-lignes] .ax100-badge')).map(b => b.className)")
    verifier(any('ax100-document' in t for t in types) and any('ax100-note_ia' in t for t in types) and any('ax100-proposition' in t for t in types),
             'liste mixte attendue (proposition, document, note de l’IA) : %r' % types)
    # filtre « Documents » : seule la ligne du document ; préférence mémorisée (POST), retrouvée au rechargement
    p.locator('[data-ax100-filtre="document"]').first.click()
    p.wait_for_function("() => Array.from(document.querySelectorAll('[data-ax100-lignes] li')).every(li => li.getAttribute('data-type') === 'document' || li.querySelector('.ax100-document'))",
                        timeout=10000)
    p.wait_for_timeout(300)
    with banc.desk() as d:
        from agent import relecture56101 as r1
        memorise = r1.filtre_memorise(d, 'cabinet')
    verifier(memorise == 'document', 'filtre non mémorisé pour la personne : %r' % memorise)
    # document : aperçu de la version exacte, puis proposition de l'IA en conflit avec une édition humaine
    p.locator('[data-ax100-lignes] li[data-review-id="%s"] a' % doc_id).click()
    p.wait_for_function("() => { const t = document.querySelector('[data-ax100-texte]'); return t && t.value.indexOf('mille euros') >= 0; }", timeout=15000)
    with banc.desk() as d:
        from agent import livrables5689 as liv, propositions5689 as patches
        row = liv.charger(d, doc['document_id'])
        patches.proposer(d, row, {'cle': 'cle-p101r-0001', 'version_id': row['version_id'], 'sha256': row['sha256'],
                                  'operations': [{'ancre': 'Le prix est de mille euros.', 'texte': 'Le prix est de deux mille euros.', 'motif': 'Consigne fictive'}]},
                         'cabinet', origin='ia')
    p.locator('[data-ax100-lecteur] [data-section="propositions"]').click()
    p.wait_for_selector('[data-ax101-proposition][data-ax101-decision="accepter"]', timeout=10000)
    verifier(p.locator('.ax101-avant-apres del').first.inner_text().strip() == 'Le prix est de mille euros.', 'avant/après non affiché')
    humain = nav.docx_fictif('Contrat fictif modifié à la main.', 'Le prix est de mille cinq cents euros.', 'Fin du contrat.')
    fichier = banc.cloud / doc['path'].lstrip('/')
    fichier.write_bytes(humain)                                       # édition humaine concurrente dans le stockage
    s.propre('À relire 5.6.101 avant le conflit')
    p.locator('[data-ax101-proposition][data-ax101-decision="accepter"]').first.click()
    p.wait_for_function("() => /rien n’a été écrasé/.test(document.querySelector('[data-ax100-statut]').textContent)", timeout=15000)
    p.wait_for_timeout(200)
    s.erreurs[:] = [x for x in s.erreurs if 'status of 409' not in x]   # refus 409 attendu : c'est la preuve du conflit sans écrasement
    verifier(fichier.read_bytes() == humain, 'le fichier modifié à la main a été écrasé')
    s.capture(sortie / 'P101r-conflit-1920.png')
    # note de l'IA : validée sans rechargement ; événements du contrat reçus, sans identifiant ni texte
    s.aller('/courriels?source=note_ia')
    p.evaluate(ECOUTE)
    p.locator('[data-ax100-lignes] li[data-review-id="%s"] a' % note_id).click()
    p.wait_for_function("() => { const b = document.querySelector('[data-ax100-actions] [data-action=\"valider\"]'); return b && !b.disabled && !b.hidden; }",
                        timeout=10000)
    p.locator('[data-ax100-actions] [data-action="valider"]').click()
    p.wait_for_function("() => (window.__p101r || []).some(e => e[0] === 'ax101:decide')", timeout=10000)
    evenements = p.evaluate('window.__p101r')
    verifier(['ax101:ouvert', '{"type":"note_ia"}'] in evenements and ['ax101:decide', '{"type":"note_ia","commande":"valider"}'] in evenements,
             'événements du contrat lecteur → mesure inattendus : %r' % evenements)
    verifier(not any(rid in e[1] or 'prix' in e[1] for e in evenements), 'un événement contient un identifiant ou du texte')
    with banc.desk() as d:
        etat = d.db.execute('SELECT etat FROM delta56100_resultats WHERE id=?', (rid,)).fetchone()[0]
    verifier(etat == 'retenu', 'note non validée chez son producteur : %s' % etat)
    s.propre('À relire 5.6.101')
    # 390 px : un seul panneau, « Retour à la liste », aucun défilement horizontal
    p.set_viewport_size({'width': 390, 'height': 844})
    s.aller('/courriels?source=tous')
    verifier(p.evaluate(SANS_DEFILEMENT), 'défilement horizontal à 390 px (liste)')
    premiere = p.locator('[data-ax100-lignes] li[data-review-id] a').first
    if premiere.count():
        premiere.click()
        p.wait_for_function("() => document.querySelector('[data-ax100-lecteur]').getAttribute('data-id')", timeout=10000)
        verifier(p.locator('[data-ax100-retour]').is_visible(), '« Retour à la liste » absent à 390 px')
        verifier(p.evaluate(SANS_DEFILEMENT), 'défilement horizontal à 390 px (lecteur)')
    s.capture(sortie / 'P101r-390.png')
    s.propre('À relire 5.6.101 à 390 px')
