"""5.6.107 (lot V) : voix de synthèse locale Kokoro (voix ``ff_siwis`` (français), ``af_heart`` (anglais), ``ef_dora`` (espagnol)) pour la vidéo de démonstration.

Lancé par ``voix.py`` dans un processus à part (torch et numpy restent hors du processus appelant). Hors ligne obligatoire :
``HF_HUB_OFFLINE=1`` et ``TRANSFORMERS_OFFLINE=1`` sont posés avant tout import ; le modèle, sa configuration et la voix sont lus dans le
dossier local (variable ``KOKORO_MODELE``), jamais téléchargés : un fichier absent est une erreur.

Usage : ``python synthese-kokoro.py DEMANDE.json RESULTAT.json`` ; demande : {modele, voix, lang_code, vitesse, travaux: [{id, texte, wav}]} ;
résultat : {voix, phonemes: {id: texte phonétique}} ; chaque WAV est écrit en PCM 16 bits mono à 24 000 Hz.
"""
import json
import os
import sys

os.environ['HF_HUB_OFFLINE'] = '1'
os.environ['TRANSFORMERS_OFFLINE'] = '1'


def main(demande, resultat):
    d = json.loads(open(demande, encoding='utf-8').read())
    m = d['modele']
    fichiers = {'config': os.path.join(m, 'config.json'), 'modele': os.path.join(m, 'kokoro-v1_0.pth'),
                'voix': os.path.join(m, 'voices', d['voix'] + '.pt')}
    for nom, chemin in fichiers.items():
        if not os.path.isfile(chemin):
            raise SystemExit('Kokoro : fichier %s absent (%s) ; rien n’est téléchargé.' % (nom, chemin))
    import numpy as np
    import soundfile as sf
    if d.get('lang_code', 'f') in ('a', 'b'):
        # Anglais : misaki a besoin du modèle spaCy « en_core_web_sm » et le TÉLÉCHARGE s'il manque (spacy.cli.download) : interdit ici.
        import spacy
        import spacy.cli

        def refuser(*_a, **_k):
            raise SystemExit('Kokoro (anglais) : téléchargement de modèle refusé (rien n’est téléchargé sans l’accord du cabinet).')
        spacy.cli.download = refuser
        if not spacy.util.is_package('en_core_web_sm'):
            raise SystemExit('Kokoro (anglais) : le modèle spaCy « en_core_web_sm » n’est pas installé ; il faut l’installer avec l’accord '
                             'du cabinet ; rien n’a été téléchargé.')
    from kokoro import KModel, KPipeline
    model = KModel(repo_id='hexgrad/Kokoro-82M', config=fichiers['config'], model=fichiers['modele']).eval()
    pipe = KPipeline(lang_code=d.get('lang_code', 'f'), repo_id='hexgrad/Kokoro-82M', model=model)
    phonemes = {}
    for t in d['travaux']:
        morceaux, ph = [], []
        for _graphemes, phon, audio in pipe(t['texte'], voice=fichiers['voix'], speed=float(d['vitesse'])):
            ph.append(str(phon))
            morceaux.append(audio)
        # pas de np.asarray : torch et numpy 2 sont incompatibles sur ce poste ; passage par des listes Python
        signal = np.array([v for m_ in morceaux for v in m_.tolist()], dtype=np.float32)
        sf.write(t['wav'], signal, 24000, subtype='PCM_16')
        phonemes[str(t['id'])] = ' | '.join(ph)
    with open(resultat, 'w', encoding='utf-8') as f:
        json.dump({'voix': 'Kokoro %s (%s)' % (d['voix'], d.get('lang_code', 'f')), 'phonemes': phonemes}, f, ensure_ascii=False)


if __name__ == '__main__':
    main(sys.argv[1], sys.argv[2])
