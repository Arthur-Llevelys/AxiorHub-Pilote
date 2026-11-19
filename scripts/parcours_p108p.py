"""5.6.108 (lot P) : parcours P108p du banc ``scripts/parcours-navigateur.py`` (vrai navigateur, données fictives, aucun réseau, aucun modèle).

P108p — pause de l'IA distincte de la suspension des envois programmés, avec la fausse messagerie du parcours P106r
(``parcours_p106r.preparer``) et deux envois fictifs semés dans la file d'envoi du compte du banc (un retenu, heure passée ; un à venir) :
- en-tête d'Aujourd'hui : « Mettre l'IA en pause » et « Suspendre les envois programmés », deux commandes et leur état ; lien
  « 1 envoi(s) retenu(s) à décider » ;
- « Suspendre les envois programmés » (confirmation) : réglage enregistré sur le serveur, la pause de l'IA n'a pas bougé ; « Mettre l'IA en
  pause » puis reprise : la suspension n'a pas bougé ;
- le lien ouvre « Envois programmés » dans Courriels : commandes, messages retenus avec leur heure prévue, décisions (« Envoyer maintenant »,
  « Reprogrammer », « Annuler l'envoi »), aucun « Retirer » pour un retenu ;
- « Reprendre les envois programmés » : le retenu attend toujours une décision (texte de décision) ; « Annuler l'envoi » : annulé.
Non inscrit dans ``PARCOURS`` : l'intégrateur l'inscrit et le lance. Lancement seul : ``python scripts/parcours_p108p.py``.
"""
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parent.parent
NAV = None


def _nav():
    if NAV is not None:
        return NAV
    import __main__ as nav
    return nav


def verifier(condition, message):
    _nav().verifier(condition, message)


def _semer(banc):
    """Deux envois fictifs du compte du banc (compte unique du serveur : propriétaire « cabinet ») : un retenu par la suspension (heure passée), un programmé dans trois jours."""
    maintenant = time.time()
    with banc.desk() as d:
        for ident, etat, depart, objet in (('p108p-retenu', 'suspendu', maintenant - 3600, 'Relance DOS-001 (fictif)'),
                                           ('p108p-a-venir', 'en_attente', maintenant + 3 * 86400, 'Convocation DOS-002 (fictif)')):
            d.db.execute("INSERT INTO envoi_file56106(id,owner,role,uid,validity,fingerprint,matter,genre,prevu,depart,options,apercu,etat,motif,cree,maj) "
                         "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                         (ident, 'cabinet', 'administrateur', '9001' if etat == 'suspendu' else '9002', '7', 'empreinte-' + ident, 'DOS-001', 'programme',
                          depart, depart, '{}', json.dumps({'a': 'durand@example.test', 'objet': objet, 'n': 1, 'pj': 0}, ensure_ascii=False),
                          etat, 'envois_suspendus' if etat == 'suspendu' else '', maintenant - 7200, maintenant - 3600))
        d.db.commit()


def _reglages(banc):
    with banc.desk() as d:
        return bool(d.settings('missions567:pause', False)), d.settings('envois56108:suspension', None)


def p108p_pause_et_envois(banc, s):
    from parcours_p106r import preparer, restaurer
    preparer(banc)
    try:
        _semer(banc)
        s.connecter()
        p = s.page
        p.on('dialog', lambda dialogue: dialogue.accept())     # confirmations demandées par les commandes : acceptées par le parcours
        s.aller('/aujourdhui')
        ia, envois, lien = p.locator('#a92-pause'), p.locator('#p108-envois'), p.locator('#p108-retenus')
        verifier(ia.inner_text().strip() == 'Mettre l’IA en pause', 'commande de pause de l’IA : %r' % ia.inner_text())
        verifier(envois.inner_text().strip() == 'Suspendre les envois programmés' and envois.get_attribute('data-actif') == '0',
                 'commande de suspension des envois : %r' % envois.inner_text())
        verifier(lien.is_visible() and '1 envoi(s) retenu(s) à décider' in lien.inner_text(), 'lien des envois retenus absent')
        s.propre('en-tête d’Aujourd’hui')
        # 1. suspension des envois : la pause de l'IA ne bouge pas
        with p.expect_response(lambda r: '/m56106/envoi/suspendre' in r.url) as rep:
            envois.click()
        verifier(rep.value.ok and rep.value.json().get('actif') is True, 'suspension refusée')
        p.wait_for_function("document.getElementById('p108-envois').dataset.actif === '1'")
        verifier(envois.inner_text().strip() == 'Envois programmés suspendus — reprendre', 'état de la suspension non affiché')
        pause_ia, suspension = _reglages(banc)
        verifier(not pause_ia and suspension and suspension.get('actif') is True, 'réglages après suspension : %r %r' % (pause_ia, suspension))
        # 2. pause de l'IA puis reprise : la suspension ne bouge pas
        with p.expect_response(lambda r: 'automatismes/pause' in r.url) as rep:
            ia.click()
        verifier(rep.value.ok and rep.value.json().get('paused') is True, 'pause de l’IA refusée')
        verifier(_reglages(banc)[1].get('actif') is True, 'la pause de l’IA a changé la suspension des envois')
        with p.expect_response(lambda r: 'automatismes/pause' in r.url) as rep:
            ia.click()
        verifier(rep.value.ok and rep.value.json().get('paused') is False, 'reprise de l’IA refusée')
        pause_ia, suspension = _reglages(banc)
        verifier(not pause_ia and suspension.get('actif') is True, 'la reprise de l’IA a levé la suspension des envois')
        p.wait_for_timeout(300)
        verifier(ia.inner_text().strip() == 'Mettre l’IA en pause', 'libellé après reprise de l’IA : %r' % ia.inner_text())
        s.propre('pause de l’IA et suspension')
        s.capture(Path(banc.sortie) / 'P108p-aujourdhui.png')
        # 3. « Envois programmés » : commandes, retenus avec leur heure prévue, décisions
        with p.expect_navigation():
            lien.click()
        p.wait_for_selector('#e106-liste:not([hidden]) .p108-commandes', timeout=15000)
        p.wait_for_selector('.p108-retenus-liste .e106-item', timeout=15000)
        bloc = p.locator('.p108-retenus-liste')
        texte = bloc.inner_text()
        verifier('Relance DOS-001 (fictif)' in texte and 'Convocation DOS-002 (fictif)' in texte, 'retenus non listés : %r' % texte[:300])
        verifier('Départ prévu le' in texte, 'heure prévue absente : %r' % texte[:300])
        retenu = bloc.locator('.e106-item', has_text='Relance DOS-001')
        boutons = [b.strip() for b in retenu.locator('.e106-actions button').all_inner_texts()]
        verifier(boutons == ['Envoyer maintenant', 'Reprogrammer', 'Annuler l’envoi'], 'décisions proposées : %r' % boutons)
        commandes = [b.strip() for b in p.locator('.p108-commandes button').all_inner_texts()]
        verifier(commandes == ['Reprendre les envois programmés', 'Mettre l’IA en pause'], 'commandes : %r' % commandes)
        s.propre('envois programmés')
        s.capture(Path(banc.sortie) / 'P108p-envois-suspendus.png')
        # 4. reprise : le retenu attend toujours une décision ; puis « Annuler l'envoi »
        with p.expect_response(lambda r: '/m56106/envoi/suspendre' in r.url) as rep:
            p.locator('.p108-commandes button', has_text='Reprendre les envois programmés').click()
        verifier(rep.value.ok and rep.value.json().get('actif') is False and rep.value.json().get('a_decider') == 1, 'reprise : %r' % rep.value.json())
        p.wait_for_selector('.p108-retenus-liste >> text=passée pendant la suspension', timeout=10000)
        verifier(p.locator('.p108-retenus-liste .e106-item').count() == 1, 'après la reprise, seul le message à décider reste retenu')
        with banc.desk() as d:
            etats = dict(d.db.execute('SELECT id, etat FROM envoi_file56106'))
        verifier(etats == {'p108p-retenu': 'suspendu', 'p108p-a-venir': 'en_attente'}, 'états après reprise : %r' % etats)
        with p.expect_response(lambda r: '/m56106/envoi/annuler' in r.url) as rep:
            p.locator('.p108-retenus-liste .e106-item button', has_text='Annuler l’envoi').click()
        verifier(rep.value.ok and rep.value.json().get('annule') is True, 'annulation refusée')
        with banc.desk() as d:
            verifier(d.db.execute("SELECT etat FROM envoi_file56106 WHERE id='p108p-retenu'").fetchone()[0] == 'annule', 'retenu non annulé')
        p.wait_for_timeout(500)
        s.propre('décision après la reprise')
    finally:
        restaurer(banc)


def main(argv=None):
    """Lancement seul (sans inscription dans ``PARCOURS``) : banc chargé comme module, parcours ajouté."""
    global NAV
    import argparse
    import importlib.util
    import tempfile
    parser = argparse.ArgumentParser()
    parser.add_argument('--sortie', default=str(Path(tempfile.gettempdir()) / 'axiorhub-parcours-p108p'))
    parser.add_argument('--navigateur', default='chromium')
    args = parser.parse_args(argv)
    sys.path.insert(0, str(ROOT))
    sys.path.insert(0, str(ROOT / 'scripts'))
    spec = importlib.util.spec_from_file_location('parcours_navigateur', str(ROOT / 'scripts' / 'parcours-navigateur.py'))
    NAV = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(NAV)
    import parcours_p106r
    parcours_p106r.NAV = NAV
    NAV.PARCOURS = NAV.PARCOURS + (('P108p', 'Pause de l’IA distincte de la suspension des envois programmés (en-tête, Envois programmés, reprise)',
                                    p108p_pause_et_envois),)
    return NAV.lancer(args.navigateur, args.sortie, ('P108p',))


if __name__ == '__main__':
    sys.exit(main())
