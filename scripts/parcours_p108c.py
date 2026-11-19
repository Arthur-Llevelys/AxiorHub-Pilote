"""5.6.108 (lot C, B01) : parcours P108c du banc ``scripts/parcours-navigateur.py`` (vrai navigateur, données fictives, aucun réseau, aucun modèle).

P108c — zéro perte de texte dans la rédaction d'un courriel, avec la fausse messagerie du parcours P106r (``parcours_p106r.preparer``) et
un enregistrement du brouillon RALENTI à volonté côté serveur (``webmail5628.save`` retenu par un verrou, dans le processus du banc) :
- frappe pendant un enregistrement lent : l'état passe par « Enregistrement du brouillon… » puis « Modifications à enregistrer… », jamais
  « Enregistré à … » tant que la dernière frappe n'est pas acquittée ; le tour suivant enregistre la saisie complète ;
- fermeture de l'onglet avec une saisie non acquittée : l'avertissement (« beforeunload ») est demandé ; après acquittement, il ne l'est plus ;
- « Annuler » avec une saisie non enregistrée : confirmation demandée (refusée : la rédaction reste, texte compris) ;
- panne de l'enregistrement : le texte reste, l'heure du prochain essai est annoncée ;
- pièce jointe ajoutée pendant le transfert : gardée dans la rédaction et présente dans le brouillon suivant ;
- changement de message (autre courriel ouvert dans la liste) : les dernières frappes sont enregistrées avant le remplacement.
Non inscrit dans ``PARCOURS`` : l'intégrateur l'inscrit et le lance. Lancement seul : ``python scripts/parcours_p108c.py``.
"""
import email
from email import policy
import json
from pathlib import Path
import sys
import threading

ROOT = Path(__file__).resolve().parent.parent
NAV = None
MESURES = {}


def _nav():
    if NAV is not None:
        return NAV
    import __main__ as nav
    return nav


def verifier(condition, message):
    _nav().verifier(condition, message)


class Frein:
    """Enregistrement du brouillon retenu côté serveur : ``retenir()`` puis ``liberer()`` ; ``panne`` : refus franc (messagerie injoignable)."""

    def __init__(self):
        self.porte = threading.Event()
        self.porte.set()
        self.arrives = threading.Event()
        self.panne = False
        self.origine = None

    def installer(self):
        from agent import webmail5628
        self.origine = webmail5628.save
        frein = self

        def save(*args, **kwargs):
            frein.arrives.set()
            frein.porte.wait(30)
            if frein.panne:
                from agent.common import Stop
                raise Stop('connexion_imap_echouee')
            return frein.origine(*args, **kwargs)
        webmail5628.save = save

    def retirer(self):
        from agent import webmail5628
        if self.origine is not None:
            webmail5628.save = self.origine

    def retenir(self):
        self.arrives.clear()
        self.porte.clear()

    def liberer(self):
        self.porte.set()


def _attendre(p, evenement, secondes=10):
    """Attente qui laisse tourner la boucle de Playwright (les requêtes passent par le filtre du banc, servi par ce fil)."""
    for _ in range(int(secondes * 10)):
        if evenement.is_set():
            return True
        p.wait_for_timeout(100)
    return evenement.is_set()


def _corps(serveur, uid):
    brut = serveur.boites['Drafts']['uids'][int(uid)][1]
    msg = email.message_from_bytes(brut, policy=policy.default)
    return msg.get_body(preferencelist=('html', 'plain')).get_content(), [p.get_filename() for p in msg.iter_attachments()]


def _etat(form):
    return form.page.evaluate("() => { const n = document.querySelector('form.bx5628-compose .bx56106r-auto') || document.querySelector('.bx56106r-auto');"
                              " return n ? n.textContent : '(état absent)'; }")


def p108c_sans_perte(banc, s):
    from parcours_p106r import ouvrir_repondre_tous, preparer, restaurer
    serveur = preparer(banc)
    frein = Frein()
    frein.installer()
    try:
        s.connecter()
        p = s.page
        p.set_viewport_size({'width': 1920, 'height': 950})
        ouvrir_repondre_tous(s)
        form = p.locator('form.bx5628-compose')
        editor = form.locator('.bx5628-editor')
        sale = "() => document.querySelector('form.bx5628-compose').axhAuto.sale()"
        editor.click()
        p.keyboard.type('Version un du projet DOS-001.')
        # 1. enregistrement lent + frappe pendant la requête
        frein.retenir()
        with p.expect_response(lambda r: '/m5628/brouillon' in r.url and r.request.method == 'POST', timeout=40000) as rep:
            form.locator('.bx5628-actions button', has_text='Enregistrer le brouillon').click()
            verifier(_attendre(p, frein.arrives), 'enregistrement non parti')
            editor.click()                                   # le focus reste sur le bouton : retour dans le texte (sinon raccourcis clavier)
            p.keyboard.press('Control+End')
            p.keyboard.type(' Ajout pendant l’enregistrement.')
            pendant = _etat(form)
            frein.liberer()
        uid1 = rep.value.json()['uid']
        p.wait_for_timeout(300)
        apres = _etat(form)
        MESURES['etats'] = {'pendant': pendant, 'apres': apres}
        verifier(p.evaluate(sale), 'la frappe faite pendant l’enregistrement est déclarée enregistrée')
        verifier(apres.startswith('Modifications à enregistrer'), 'état après une réponse ancienne : « %s »' % apres)
        verifier('Ajout pendant' not in _corps(serveur, uid1)[0], 'le premier dépôt devait être l’instantané d’avant la frappe')
        # 2. fermeture de l'onglet : avertissement tant que la saisie n'est pas acquittée
        bloque = p.evaluate("() => { const e = new Event('beforeunload', {cancelable: true}); window.dispatchEvent(e); return e.defaultPrevented; }")
        verifier(bloque, 'fermeture sans avertissement alors qu’une frappe n’est pas enregistrée')
        # 3. « Annuler » : confirmation refusée, la rédaction reste
        p.once('dialog', lambda d: d.dismiss())
        form.locator('.bx5628-actions button', has_text='Annuler').click()
        verifier(p.locator('form.bx5628-compose').count() == 1 and 'Ajout pendant' in editor.inner_text(), 'texte perdu après « Annuler » refusé')
        # 4. tour suivant : la saisie complète est enregistrée, plus d'avertissement
        with p.expect_response(lambda r: '/m5628/brouillon' in r.url and r.request.method == 'POST', timeout=40000) as rep:
            p.evaluate("() => document.querySelector('form.bx5628-compose').axhAuto.tour()")
        uid2 = rep.value.json()['uid']
        p.wait_for_timeout(300)
        verifier('Ajout pendant' in _corps(serveur, uid2)[0], 'la frappe faite pendant l’enregistrement n’a pas été enregistrée')
        verifier(_etat(form).startswith('Enregistré à') and not p.evaluate(sale), 'état final : « %s »' % _etat(form))
        verifier(not p.evaluate("() => { const e = new Event('beforeunload', {cancelable: true}); window.dispatchEvent(e); return e.defaultPrevented; }"),
                 'avertissement de fermeture alors que tout est enregistré')
        verifier(int(uid1) not in serveur.boites['Drafts']['uids'], 'l’ancien brouillon n’a pas été remplacé')
        s.propre('enregistrement lent et frappe pendant la requête')
        # 5. panne : texte gardé, prochain essai annoncé
        editor.click()
        p.keyboard.press('Control+End')
        p.keyboard.type(' Texte écrit pendant la panne.')
        frein.panne = True
        with p.expect_response(lambda r: '/m5628/brouillon' in r.url and r.request.method == 'POST', timeout=40000):
            p.evaluate("() => document.querySelector('form.bx5628-compose').axhAuto.tour()")
        p.wait_for_timeout(300)
        verifier('nouvel essai à' in _etat(form) and 'pendant la panne' in editor.inner_text(), 'panne : « %s »' % _etat(form))
        frein.panne = False
        s.erreurs.clear()   # la réponse d'erreur du serveur est attendue ici
        # 6. pièce ajoutée pendant le transfert
        MESURES['avant_piece'] = p.evaluate("() => { const f = document.querySelector('form.bx5628-compose'); return f ? {sale: f.axhAuto.sale(), "
                                            "etat: (f.querySelector('.bx56106r-auto') || {}).textContent, dedans: document.contains(f)} : null; }")
        frein.retenir()
        with p.expect_response(lambda r: '/m5628/brouillon' in r.url and r.request.method == 'POST', timeout=40000) as rep:
            p.evaluate("() => document.querySelector('form.bx5628-compose').axhAuto.tour()")
            verifier(_attendre(p, frein.arrives), 'enregistrement non parti (pièce)')
            form.locator('input[type=file]').first.set_input_files(files=[{'name': 'piece-DOS-001.txt', 'mimeType': 'text/plain',
                                                                          'buffer': 'Pièce fictive du dossier DOS-001'.encode('utf-8')}])
            frein.liberer()
        p.wait_for_timeout(400)
        verifier('piece-DOS-001.txt' in form.inner_text(), 'pièce ajoutée pendant le transfert retirée de la rédaction')
        with p.expect_response(lambda r: '/m5628/brouillon' in r.url and r.request.method == 'POST', timeout=40000) as rep:
            p.evaluate("() => document.querySelector('form.bx5628-compose').axhAuto.tour()")
        texte, pieces = _corps(serveur, rep.value.json()['uid'])
        verifier('piece-DOS-001.txt' in pieces and 'pendant la panne' in texte, 'brouillon suivant : pièces %s' % pieces)
        s.propre('panne et pièce ajoutée pendant le transfert')
        # 7. changement de message : dernières frappes enregistrées avant le remplacement
        editor.click()
        p.keyboard.press('Control+End')
        p.keyboard.type(' Dernière phrase avant de changer de message.')
        with p.expect_response(lambda r: '/m5628/brouillon' in r.url and r.request.method == 'POST', timeout=40000) as rep:
            p.locator('#bx5625-rows .ax-row[data-uid]').nth(1).click()
        texte, _ = _corps(serveur, rep.value.json()['uid'])
        verifier('Dernière phrase avant de changer de message' in texte, 'frappes perdues au changement de message')
        verifier(len(serveur.boites['Drafts']['uids']) == 1, 'brouillons en double : %d' % len(serveur.boites['Drafts']['uids']))
        MESURES['brouillons'] = len(serveur.boites['Drafts']['uids'])
        p.screenshot(path=str(Path(getattr(banc, 'sortie', '.') or '.') / 'P108c-sans-perte.png'))
        s.propre('changement de message')
    finally:
        frein.liberer()
        frein.retirer()
        restaurer(banc)


def main(argv=None):
    """Lancement seul (sans inscription dans ``PARCOURS``) : banc chargé comme module, parcours ajouté, mesures écrites dans la sortie."""
    global NAV
    import argparse
    import importlib.util
    import tempfile
    parser = argparse.ArgumentParser()
    parser.add_argument('--sortie', default=str(Path(tempfile.gettempdir()) / 'axiorhub-parcours-p108c'))
    parser.add_argument('--navigateur', default='chromium')
    args = parser.parse_args(argv)
    sys.path.insert(0, str(ROOT))
    sys.path.insert(0, str(ROOT / 'scripts'))
    spec = importlib.util.spec_from_file_location('parcours_navigateur', str(ROOT / 'scripts' / 'parcours-navigateur.py'))
    NAV = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(NAV)
    import parcours_p106r
    parcours_p106r.NAV = NAV
    NAV.PARCOURS = NAV.PARCOURS + (('P108c', 'Courriels : zéro perte de texte (enregistrement lent, fermeture, panne, pièce, changement de message)',
                                    p108c_sans_perte),)
    code = NAV.lancer(args.navigateur, args.sortie, ('P108c',))
    Path(args.sortie).mkdir(parents=True, exist_ok=True)
    (Path(args.sortie) / 'P108c-mesures.json').write_text(json.dumps(MESURES, ensure_ascii=False, indent=1), encoding='utf-8')
    print(json.dumps(MESURES, ensure_ascii=False, indent=1))
    return code


if __name__ == '__main__':
    sys.exit(main())
