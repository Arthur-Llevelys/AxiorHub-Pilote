"""5.6.107 (lot V) : voix de synthèse locale de la vidéo de démonstration, un fichier WAV par plan, sans aucun service en ligne.

- ``kokoro`` (défaut quand le modèle est présent, choix de l'avocat le 17/11/2026) : Kokoro-82M, voix française ``ff_siwis``, modèle lu
  dans le dossier local ``KOKORO_MODELE`` (sinon réglage ``voix.kokoro.modele``), hors ligne obligatoire (``synthese-kokoro.py``, processus
  à part) ; aucun téléchargement ;
- ``windows`` : synthèse vocale du système (``System.Speech`` par PowerShell, ``synthese-windows.ps1``) avec la première voix française
  installée parmi les préférences des réglages (sur le poste de développement : « Microsoft Hortense Desktop », fr-FR) ;
- ``piper`` (allemand : voix ``de_DE-thorsten-high``) : programme et voix locaux (variables ``PIPER``, ``PIPER_VOIX``), texte sur l'entrée
  standard, aucun réseau ;
- ``espeak-ng`` : repli (voix de la langue). Moteur, voix et vitesse par langue : ``langues.json``.
- Bords de chaque phrase (``bords``, corrigé le 17/11/2026 : « premier mot coupé ») : la synthèse attaque la première syllabe sans
  préparation ; Kokoro reçoit une pause de tête dans le texte (« … », réglage ``tete_texte`` de la langue), puis chaque WAV est ramené à
  ``tete_s`` (250 ms) de silence avant le premier son et ``queue_s`` (150 ms) après le dernier, avec un fondu d'entrée de 10 ms sur le
  silence ; seul le silence en trop est retiré (marge de 30 ms gardée avant le premier son), jamais le son.
- ``duree_wav`` mesure chaque fichier ; ``assembler`` concatène les voix dans un seul WAV en plaçant chacune au début réel de son plan
  (silences entre les plans), en Python seulement : le montage n'a plus qu'à mixer ce WAV avec l'image.
"""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import wave

from . import texte as tx

ICI = Path(__file__).resolve().parent


class ErreurVoix(RuntimeError):
    """Aucune synthèse vocale locale utilisable."""


def duree_wav(chemin):
    with wave.open(str(chemin), 'rb') as w:
        return w.getnframes() / float(w.getframerate())


MOTEURS = ('kokoro', 'piper', 'windows', 'espeak-ng')


def _chemin(valeur):
    chemin = Path(valeur)
    return chemin if chemin.is_absolute() else (tx.RACINE / chemin).resolve()


def dossier_kokoro(regl=None, environ=None):
    """Dossier du modèle Kokoro : variable ``KOKORO_MODELE``, sinon réglage (relatif à la racine du dépôt)."""
    environ = os.environ if environ is None else environ
    return _chemin(str(environ.get('KOKORO_MODELE') or '').strip() or (regl or tx.reglages())['voix']['kokoro']['modele'])


def kokoro_pret(regl=None, environ=None, code='fr'):
    """Vrai si la bibliothèque ``kokoro`` est installée et le modèle complet présent (configuration, poids, voix de la langue)."""
    regl = regl or tx.reglages()
    d = dossier_kokoro(regl, environ)
    voix = tx.langue(code)['voix'].get('voix', '') if tx.langue(code)['voix']['moteur'] == 'kokoro' else 'ff_siwis'
    fichiers = (d / 'config.json', d / 'kokoro-v1_0.pth', d / 'voices' / (voix + '.pt'))
    return all(f.is_file() for f in fichiers) and importlib.util.find_spec('kokoro') is not None


def piper_chemins(regl=None, environ=None, code='de'):
    """(programme, modèle .onnx) de Piper : variables ``PIPER`` et ``PIPER_VOIX`` (dossier des voix), sinon réglages."""
    environ = os.environ if environ is None else environ
    p = (regl or tx.reglages())['voix']['piper']
    programme = _chemin(str(environ.get('PIPER') or '').strip() or p['programme'])
    voix = _chemin(str(environ.get('PIPER_VOIX') or '').strip() or p['voix'])
    return programme, voix / (tx.langue(code)['voix']['voix'] + '.onnx')


def piper_pret(regl=None, environ=None, code='de'):
    programme, modele = piper_chemins(regl, environ, code)
    return programme.is_file() and modele.is_file() and Path(str(modele) + '.json').is_file()


def pret(m, regl=None, code='fr'):
    if m == 'kokoro':
        return tx.langue(code)['voix']['moteur'] == 'kokoro' and kokoro_pret(regl, code=code)
    if m == 'piper':
        return tx.langue(code)['voix']['moteur'] == 'piper' and piper_pret(regl, code=code)
    if m == 'windows':
        return bool(tx.langue(code)['voix'].get('windows')) and os.name == 'nt' and bool(shutil.which('powershell'))
    if m == 'espeak-ng':
        return bool(shutil.which('espeak-ng'))
    return False


def moteur(regl=None, code='fr'):
    """Moteur de la langue (``langues.json`` : kokoro, piper) s'il est prêt, sinon ses replis dans l'ordre, sinon None."""
    v = tx.langue(code)['voix']
    for m in [v['moteur']] + list(v.get('repli', [])):
        if pret(m, regl, code):
            return m
    return None


def synthetiser(plans_voix, dossier, regl=None, choix=None, code='fr'):
    """Écrit ``plan-01.wav`` … dans ``dossier`` ; rend ``{'moteur', 'voix', 'langue', 'durees': {id: s}, 'fichiers': {id: chemin}}``."""
    regl = regl or tx.reglages()
    v, conf = regl['voix'], tx.langue(code)
    dossier = Path(dossier)
    dossier.mkdir(parents=True, exist_ok=True)
    m = {'espeak': 'espeak-ng'}.get(choix, choix) or moteur(regl, code)
    tete = conf['voix'].get('tete_texte', '') if m in ('kokoro', 'piper') else ''
    travaux = [{'id': p['id'], 'texte': tete + tx.texte_parle(p['texte'], conf.get('prononciation', {})),
                'wav': str(dossier / ('plan-%02d.wav' % p['id']))} for p in plans_voix if p['texte']]
    if m == 'kokoro':
        if not pret('kokoro', regl, code):
            raise ErreurVoix('Kokoro indisponible pour « %s » : bibliothèque kokoro ou modèle absent de %s (variable KOKORO_MODELE)'
                             % (code, dossier_kokoro(regl)))
        voix = _kokoro(travaux, conf['voix'], dossier_kokoro(regl))
    elif m == 'piper':
        if not pret('piper', regl, code):
            raise ErreurVoix('Piper indisponible pour « %s » : programme ou voix absents (%s ; variables PIPER, PIPER_VOIX)'
                             % (code, ', '.join(str(x) for x in piper_chemins(regl, code=code))))
        voix = _piper(travaux, conf['voix'], *piper_chemins(regl, code=code))
    elif m == 'windows' and pret('windows', regl, code):
        voix = _windows(travaux, dict(v['windows'], **tx.langue(code)['voix']['windows']))
    elif m == 'espeak-ng':
        voix = _espeak(travaux, dict(v['linux'], voix=conf['voix'].get('espeak', v['linux']['voix'])))
    else:
        raise ErreurVoix('aucune synthèse vocale locale pour « %s » (moteur %r)' % (code, m))
    out = {'moteur': m, 'voix': voix, 'langue': code, 'durees': {}, 'fichiers': {}, 'bords': {}}
    for t in travaux:
        out['bords'][t['id']] = bords(t['wav'], float(v.get('tete_s', 0.25)), float(v.get('queue_s', 0.15)), float(v.get('fondu_s', 0.01)))
        out['durees'][t['id']] = round(duree_wav(t['wav']), 3)
        out['fichiers'][t['id']] = t['wav']
        if t.get('phonemes'):
            out.setdefault('phonemes', {})[t['id']] = t['phonemes']   # contrôle de la prononciation sans écoute
    (dossier / 'durees.json').write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding='utf-8')
    return out


def _kokoro(travaux, k, modele):
    with tempfile.TemporaryDirectory(prefix='axh-video56107-') as tmp:
        demande, resultat = Path(tmp) / 'demande.json', Path(tmp) / 'resultat.json'
        demande.write_text(json.dumps({'modele': str(modele), 'voix': k['voix'], 'lang_code': k.get('lang_code', 'f'),
                                       'vitesse': float(k['vitesse']), 'travaux': travaux}, ensure_ascii=False), encoding='utf-8')
        env = dict(os.environ, HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', PYTHONIOENCODING='utf-8')
        r = subprocess.run([sys.executable, str(ICI / 'synthese-kokoro.py'), str(demande), str(resultat)], env=env,
                           capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=1800)
        if r.returncode != 0 or not resultat.exists():
            raise ErreurVoix('synthèse Kokoro en échec : %s' % (r.stderr or r.stdout).strip()[-600:])
        donnees = json.loads(resultat.read_text(encoding='utf-8'))
        for t in travaux:
            t['phonemes'] = donnees.get('phonemes', {}).get(str(t['id']), '')
        return donnees['voix']


def commande_piper(programme, modele, vitesse=1.0):
    """Ligne de commande de Piper (texte sur l'entrée standard, son brut sur la sortie standard) ; ``--length_scale`` = 1 / vitesse.

    ``--output_raw`` : le son de tout le texte arrive sur la sortie standard ; ``_piper`` l'écrit en WAV à la fréquence du modèle (un
    seul format maîtrisé, quel que soit le découpage en phrases fait par Piper)."""
    return [str(programme), '-m', str(modele), '--output_raw', '-q', '--length_scale', '%.3f' % (1.0 / float(vitesse))]


def _piper(travaux, p, programme, modele):
    taux = int(json.loads(Path(str(modele) + '.json').read_text(encoding='utf-8')).get('audio', {}).get('sample_rate', 22050))
    for t in travaux:
        r = subprocess.run(commande_piper(programme, modele, p.get('vitesse', 1.0)), input=t['texte'].encode('utf-8'),
                           capture_output=True, timeout=600, cwd=str(Path(programme).parent))
        if r.returncode != 0 or len(r.stdout) < 2 * taux // 10:
            raise ErreurVoix('Piper en échec : %s' % r.stderr.decode('utf-8', 'replace').strip()[-400:])
        with wave.open(t['wav'], 'wb') as w:       # PCM 16 bits mono à la fréquence du modèle
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(taux)
            w.writeframes(r.stdout[:len(r.stdout) // 2 * 2])
    return 'Piper %s' % Path(modele).stem


def _windows(travaux, w):
    with tempfile.TemporaryDirectory(prefix='axh-video56107-') as tmp:
        demande, resultat = Path(tmp) / 'demande.json', Path(tmp) / 'resultat.json'
        demande.write_text(json.dumps({'travaux': travaux, 'preferees': w['voix_preferees'], 'culture': w['culture'],
                                       'debit': int(w['debit']), 'volume': int(w['volume']), 'frequence': int(w['frequence_hz'])},
                                      ensure_ascii=False), encoding='utf-8')
        r = subprocess.run(['powershell', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File',
                            str(ICI / 'synthese-windows.ps1'), '-Demande', str(demande), '-Resultat', str(resultat)],
                           capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=600)   # console OEM : décodage tolérant
        if r.returncode != 0 or not resultat.exists():
            raise ErreurVoix('synthèse Windows en échec : %s' % (r.stderr or r.stdout).strip()[:500])
        return json.loads(resultat.read_text(encoding='utf-8-sig'))['voix']


def _espeak(travaux, l):
    for t in travaux:
        r = subprocess.run([l.get('programme', 'espeak-ng'), '-v', l['voix'], '-s', str(int(l['mots_par_minute'])), '-w', t['wav'], t['texte']],
                           capture_output=True, text=True, timeout=120)
        if r.returncode != 0:
            raise ErreurVoix('espeak-ng en échec : %s' % r.stderr.strip()[:300])
    return 'espeak-ng ' + l['voix']


SEUIL_RELATIF, SEUIL_MIN, MARGE_SON = 0.01, 64, 0.03


def debut_fin_son(ech, seuil):
    """Indices du premier et du dernier échantillon d'amplitude au moins ``seuil`` ; (None, None) pour un silence."""
    debut = next((i for i, x in enumerate(ech) if abs(x) >= seuil), None)
    if debut is None:
        return None, None
    fin = next(i for i in range(len(ech) - 1, -1, -1) if abs(ech[i]) >= seuil)
    return debut, fin


def bords(chemin, tete=0.25, queue=0.15, fondu=0.01):
    """Réécrit le WAV (PCM 16 bits) avec exactement ``tete`` s de silence avant le premier son et ``queue`` s après le dernier.

    Premier et dernier son : amplitude au moins 1 % du pic (et au moins 64) ; une marge de 30 ms est gardée de part et d'autre (souffle,
    attaque d'une consonne faible) : seul le silence en trop est retiré, jamais le son. Fondu d'entrée de ``fondu`` s sur le début gardé
    (silence de la marge), pour éviter un clic. Rend les mesures (ms) du son retenu."""
    import array
    with wave.open(str(chemin), 'rb') as w:
        taux, largeur, canaux = w.getframerate(), w.getsampwidth(), w.getnchannels()
        brut = w.readframes(w.getnframes())
    if largeur != 2 or canaux != 1:
        raise ErreurVoix('WAV inattendu (%d octets, %d canaux) : %s' % (largeur, canaux, chemin))
    ech = array.array('h', brut)
    pic = max((abs(x) for x in ech), default=0)
    debut, fin = debut_fin_son(ech, max(SEUIL_MIN, int(SEUIL_RELATIF * pic)))
    if debut is None:
        raise ErreurVoix('WAV sans voix : %s' % chemin)
    marge = int(MARGE_SON * taux)
    a, b = max(0, debut - marge), min(len(ech), fin + 1 + marge)
    garde = ech[a:b]
    n = min(int(fondu * taux), len(garde))
    for i in range(n):
        garde[i] = int(garde[i] * i / float(n))
    avant, apres = max(0, int(tete * taux) - (debut - a)), max(0, int(queue * taux) - (b - 1 - fin))
    sortie = array.array('h', [0]) * avant + garde + array.array('h', [0]) * apres
    with wave.open(str(chemin), 'wb') as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(taux)
        w.writeframes(sortie.tobytes())
    return {'silence_retire_avant_ms': round((a - 0) * 1000.0 / taux), 'premier_son_ms': round((avant + debut - a) * 1000.0 / taux),
            'silence_apres_ms': round((apres + b - 1 - fin) * 1000.0 / taux)}


def assembler(fichiers, debuts, duree_totale, sortie, avance=0.0):
    """Un seul WAV de ``duree_totale`` secondes : la voix du plan ``id`` commence à ``debuts[id] + avance`` (silence ailleurs).

    Tous les WAV doivent avoir le même format (fréquence, échantillons, canaux), sinon ``ErreurVoix``. Une voix qui déborderait sur la
    suivante est décalée après elle (jamais de chevauchement), et la durée totale est allongée si nécessaire. Rend la durée écrite."""
    formats, morceaux = set(), []
    for ident in sorted(fichiers, key=lambda k: float(debuts[k])):
        with wave.open(str(fichiers[ident]), 'rb') as w:
            formats.add((w.getframerate(), w.getsampwidth(), w.getnchannels()))
            morceaux.append((float(debuts[ident]) + avance, w.readframes(w.getnframes())))
    if len(formats) != 1:
        raise ErreurVoix('formats WAV différents : %r' % sorted(formats))
    taux, largeur, canaux = formats.pop()
    trame = largeur * canaux
    tampon = bytearray()
    for debut, donnees in morceaux:
        position = max(int(round(debut * taux)) * trame, len(tampon))
        tampon += b'\x00' * (position - len(tampon))
        tampon += donnees
    fin = max(int(round(float(duree_totale) * taux)) * trame, len(tampon))
    tampon += b'\x00' * (fin - len(tampon))
    with wave.open(str(sortie), 'wb') as w:
        w.setnchannels(canaux)
        w.setsampwidth(largeur)
        w.setframerate(taux)
        w.writeframes(bytes(tampon))
    return len(tampon) / float(trame * taux)
