"""5.6.110 (lot D) : parcours P110d du banc ``scripts/parcours-navigateur.py`` (vrai navigateur, version serveur du banc, données fictives,
aucun réseau, aucun vrai moteur ni micro).

P110d-dictee :
- moteur simulé (dossier désigné par ``AXIORHUB_DICTEE``), modèle minuscule déposé dans le dossier d'état du banc, dictée du poste active ;
- Paramètres › Voix : carte « Dictée sur ce poste » visible au-dessus de « Moteurs de voix », dictée active, « Vérifier le moteur » et la
  zone d'essai présents, balise ``axiorhub-audio`` à ``enabled`` et ``v56110-dictee.js`` chargé ;
- conversion réelle dans le navigateur : son fabriqué par un oscillateur, enregistré par ``MediaRecorder`` sur un
  ``MediaStreamDestination`` (aucun micro), envoyé en webm/opus à ``/dictation`` comme le font les scripts de dictée ; le faux moteur du
  banc le reçoit en ``audio/wav`` 16 kHz mono 16 bits et rend un texte fictif, inséré tel quel ;
- thèmes clair et sombre, aucun défilement horizontal à 375 × 812 ; aucune erreur JavaScript ni CSP, aucune requête externe.
Non inscrit dans ``PARCOURS`` : l'intégrateur l'inscrit.
Lancement seul : ``python scripts/parcours_p110d.py [--navigateur msedge] [--sortie DOSSIER]``.
"""
from pathlib import Path
import hashlib
import os
import struct
import sys

ROOT = Path(__file__).resolve().parent.parent
NAV = None
SANS_DEFILEMENT = 'document.documentElement.scrollWidth <= window.innerWidth + 1'
TEXTE = 'Maître DURAND, texte fictif du banc pour le dossier DOS-001.'
ENREGISTRER = """async (args) => {
  const [prefix, csrf] = args;
  const ctx = new AudioContext();
  try { await ctx.resume(); } catch (e) { /* lecture automatique refusée : l'oscillateur produit quand même le flux */ }
  const osc = ctx.createOscillator(), dest = ctx.createMediaStreamDestination(), gain = ctx.createGain();
  osc.frequency.value = 440; gain.gain.value = 0.5; osc.connect(gain); gain.connect(dest); osc.start();
  const mime = ['audio/webm;codecs=opus', 'audio/ogg;codecs=opus', 'audio/mp4'].find(x => MediaRecorder.isTypeSupported(x));
  const rec = new MediaRecorder(dest.stream, {mimeType: mime, audioBitsPerSecond: 64000}), morceaux = [];
  rec.ondataavailable = e => { if (e.data && e.data.size) morceaux.push(e.data); };
  const fin = new Promise(ok => { rec.onstop = ok; });
  rec.start(250); await new Promise(ok => setTimeout(ok, 1500)); rec.stop(); await fin; osc.stop(); await ctx.close();
  const corps = new Blob(morceaux, {type: mime});
  const r = await fetch(prefix + '/dictation', {method: 'POST', credentials: 'same-origin',
    headers: {'Content-Type': mime.split(';')[0], 'X-CSRF-Token': csrf}, body: corps});
  return {statut: r.status, envoye: mime, octets: corps.size, reponse: await r.json()};
}"""


def _nav():
    if NAV is not None:
        return NAV
    import __main__ as nav
    return nav


def verifier(condition, message):
    _nav().verifier(condition, message)


def p110d_dictee(banc, s):
    from unittest.mock import patch
    from agent import dictee56110 as D
    p = s.page
    moteur = Path(banc.base) / 'moteur été' / 'dictee'
    moteur.mkdir(parents=True)
    for nom in ('whisper-cli.exe', 'whisper-cli'):
        (moteur / nom).write_bytes(b'faux moteur du banc')
    contenu = b'modele fictif du banc ' * 100
    modele = dict(D.MODELES['rapide'], octets=len(contenu), sha256=hashlib.sha256(contenu).hexdigest())
    recu = {}

    def faux_moteur(args, cwd, delai):
        a = list(args)
        recu['args'] = a
        brut = (Path(cwd) / a[a.index('-f') + 1]).read_bytes()
        recu['entete'] = struct.unpack('<4sI4s4sIHHIIHH', brut[:36])
        recu['octets'] = len(brut)
        (Path(cwd) / (a[a.index('-of') + 1] + '.txt')).write_bytes((TEXTE + '\r\n').encode('utf-8'))
        return 0
    vrai_dicter = D.dicter

    def dicter(config, raw, content_type):
        recu['content_type'] = content_type
        recu['debut'] = raw[:4]
        return vrai_dicter(config, raw, content_type)
    with patch.dict(os.environ, {'AXIORHUB_DICTEE': str(moteur)}), patch.dict(D.MODELES, {'rapide': modele}), \
            patch.object(D, '_lancer', faux_moteur), patch.object(D, 'dicter', dicter):
        chemin = D.chemin_modele(banc.config['state_dir'], 'rapide')
        chemin.parent.mkdir(parents=True, exist_ok=True)
        chemin.write_bytes(contenu)
        with banc.desk() as d:
            d.setting(D.KEY, {'moteur': 'poste', 'modele': 'rapide', 'langue': 'fr', 'version': 1})
        verifier(D.active(banc.config), 'la dictée du poste n’est pas active dans le banc')
        p.set_viewport_size({'width': 1366, 'height': 900})
        s.connecter()
        s.aller('/parametres?rubrique=voix')
        p.wait_for_selector('#dictee56110', state='attached')
        p.click('details:has(#dictee56110) > summary')        # sections repliables de Paramètres (5.6.60)
        p.wait_for_selector('#dictee56110')
        texte = p.inner_text('#dictee56110')
        (Path(banc.sortie) / 'P110d-carte.html').write_text(p.inner_html('#dictee56110'), encoding='utf-8')
        verifier('Dictée sur ce poste : active.' in texte, 'carte : dictée non annoncée active (%s)' % texte[:200])
        for sel in ('#dictee56110 form[data-api="m56110/dictee/verifier"] button', '#d110-essai', '#dictee56110 input[type=radio][name=modele][value=rapide]'):
            verifier(p.locator(sel).count() == 1, 'carte : %s absent' % sel)
        ordre = p.evaluate("""() => { const c = document.getElementById('dictee56110');
          const h = Array.from(document.querySelectorAll('h2')).find(x => /Moteurs de voix/.test(x.textContent));
          return !!(c && h && (c.compareDocumentPosition(h) & Node.DOCUMENT_POSITION_FOLLOWING)); }""")
        verifier(ordre, 'la carte n’est pas au-dessus de « Moteurs de voix »')
        verifier(p.evaluate("document.querySelector('meta[name=axiorhub-audio]').content") == 'enabled', 'balise axiorhub-audio non activée')
        verifier(p.evaluate("!!window.ax110Dictee"), 'v56110-dictee.js non chargé')
        p.locator('#dictee56110').screenshot(path=str(Path(banc.sortie) / 'P110d-carte.png'))
        s.propre('Paramètres › Voix : carte')

        csrf = p.evaluate("document.querySelector('meta[name=axiorhub-csrf]').content")
        prefix = p.evaluate("document.querySelector('meta[name=axiorhub-prefix]').content")
        out = p.evaluate(ENREGISTRER, [prefix, csrf])
        verifier(out['statut'] == 200, 'dictée refusée : %s' % out)
        verifier(out['reponse'].get('text') == TEXTE, 'texte transcrit inattendu : %s' % out['reponse'])
        verifier(out['envoye'].startswith(('audio/webm', 'audio/ogg', 'audio/mp4')) and out['octets'] > 0, 'enregistrement non produit : %s' % out)
        verifier(recu.get('content_type') == 'audio/wav' and recu.get('debut') == b'RIFF', 'le serveur n’a pas reçu du WAV : %s' % recu.get('content_type'))
        riff, _, wave, fmt, _, codage, canaux, taux, _, _, bits = recu['entete']
        verifier((riff, wave, fmt, codage, canaux, taux, bits) == (b'RIFF', b'WAVE', b'fmt ', 1, 1, 16000, 16),
                 'WAV reçu non conforme : %s' % (recu['entete'],))
        duree = (recu['octets'] - 44) / 32000
        verifier(1.0 <= duree <= 2.5, 'durée du WAV inattendue : %.2f s' % duree)
        s.propre('conversion WAV et envoi à /dictation')

        for theme in ('light', 'dark'):
            p.evaluate("t => { document.documentElement.dataset.theme = t; }", theme)
            p.wait_for_timeout(100)
            fond = p.evaluate("getComputedStyle(document.getElementById('dictee56110')).backgroundColor")
            verifier(fond, 'thème %s : fond de la carte illisible' % theme)
            p.locator('#dictee56110').screenshot(path=str(Path(banc.sortie) / ('P110d-carte-%s.png' % theme)))
        p.set_viewport_size({'width': 375, 'height': 812})
        p.wait_for_timeout(200)
        verifier(p.evaluate(SANS_DEFILEMENT), 'défilement horizontal de Paramètres › Voix à 375 px')
        p.locator('#dictee56110').screenshot(path=str(Path(banc.sortie) / 'P110d-carte-375.png'))
        s.propre('375 px, thèmes clair et sombre')


PARCOURS = (('P110d-dictee', 'Dictée sur ce poste : carte, conversion WAV réelle dans le navigateur, faux moteur, 375 px', p110d_dictee),)


def main(argv=None):
    """Lancement seul (sans inscription dans ``PARCOURS`` du banc)."""
    global NAV
    import argparse
    import importlib.util
    import tempfile
    parser = argparse.ArgumentParser()
    parser.add_argument('--sortie', default=str(Path(tempfile.gettempdir()) / 'axiorhub-parcours-p110d'))
    parser.add_argument('--navigateur', default='chromium')
    parser.add_argument('--parcours', default=','.join(i for i, _, _ in PARCOURS))
    args = parser.parse_args(argv)
    sys.path.insert(0, str(ROOT))
    sys.path.insert(0, str(ROOT / 'scripts'))
    spec = importlib.util.spec_from_file_location('parcours_navigateur', str(ROOT / 'scripts' / 'parcours-navigateur.py'))
    NAV = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(NAV)
    if not any(ident == PARCOURS[0][0] for ident, _, _ in NAV.PARCOURS):   # déjà inscrit par le banc depuis l'intégration
        NAV.PARCOURS = NAV.PARCOURS + PARCOURS
    return NAV.lancer(args.navigateur, args.sortie, tuple(x for x in args.parcours.split(',') if x), continuer=True)


if __name__ == '__main__':
    sys.exit(main())
