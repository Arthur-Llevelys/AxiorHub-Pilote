"""5.6.94 (lot voix) : voix Brindille produite dans le navigateur, fichiers embarqués, repli serveur, Alkove pour la dictée.

- V01 : Brindille (révision Hugging Face figée) et Alkove 0.4.2 sont copiés dans ``agent/static/brindille`` et ``agent/static/alkove`` ;
  chaque fichier est déclaré ici avec son type et son empreinte sha256 (vérifiée par les tests). Aucun CDN, aucun Hugging Face à l'usage.
- V03 : fournisseur « brindille » : le serveur prépare seulement le texte (``lecture5694``), applique les droits et inscrit l'audit
  (caractères, fournisseur) ; il ne synthétise rien. C'est la voix d'une installation neuve ou d'une configuration sans fournisseur choisi ;
  un fournisseur choisi par le cabinet est conservé tel quel.
- V06 : si la voix du navigateur ne démarre pas, la lecture passe par la voix de repli du serveur (eSpeak par défaut), jamais par un
  service externe.
- V07 : Alkove (Focus, Cue) est chargé par le navigateur depuis AxiorHub seulement (``ALKOVE_CONFIG``) ; réglages personnels
  « isoler ma voix » et « arrêter quand je me tais » dans le profil de l'assistant (``assistant567``).
Aucun modèle d'IA n'est appelé : la pause de l'IA n'a pas à intervenir. Aucune tâche de fond n'est ajoutée au serveur.
"""
from .common import Stop

FOURNISSEURS = ('brindille', 'kokoro', 'espeak', 'chatterbox', 'elevenlabs')
SERVEUR = ('espeak', 'kokoro', 'chatterbox', 'elevenlabs')
REPLIS = ('espeak', 'kokoro', 'chatterbox')            # voix de repli : toujours locale (jamais ElevenLabs, service externe)
DEFAUT = 'brindille'
REPLI_DEFAUT = 'espeak'

BRINDILLE_REVISION = 'd393e3d5161937f2064bcfef33213749af4979f0'   # révision Hugging Face du modèle edbrdi/brindille
ALKOVE_VERSION = '0.4.2'                                          # paquet npm alkove, version 0.x figée (API susceptible de changer)
ALKOVE_MODELES = {   # repris du manifeste du paquet (dist/chunk-FC4ZSMUX.js) : nom, révision Hugging Face, taille, empreinte
    'focus': ('alkove-focus-1-preview', '8e91ef608366cf503fbd8aaa44bc2000dce899ed', 1111696, 'a63cda32e023b8d76089f4c3df0cc2c69ba36017b8f6cdea1da1094ef8022b6a'),
    'cue': ('alkove-cue-1-preview', '0e1f30380bdfb3201b5d4fd7d9e4197f71f11a60', 1199892, '19e8b6c66ee9ce23bba3b8011f03827c5ba4bb2fe8bec25fe07558a78950190a'),
}

JS, WASM, BINAIRE, JSON, TEXTE = 'text/javascript', 'application/wasm', 'application/octet-stream', 'application/json', 'text/plain'
# chemin sous agent/static → (type servi, sha256)
FICHIERS = {
    'brindille/tts.js': (JS, 'c365d4777f183a732093d53e8b1d5b64e6a12e3b57fe3cd25e1e2012ecfa7d61'),
    'brindille/tts.wasm': (WASM, 'b5d0644528bcb1388ff7ec527d4fcd36eb14c6c536438e7e06d622112a176c94'),
    'brindille/model/tts.alk': (BINAIRE, '65e8b4063bf99c27d509c8427b61e7867e721d62e5cad4171faf795dc02f44c6'),
    'brindille/model/lex.alk': (BINAIRE, '1e64ca9f5c2281cadae0f9c4a944cd1b1b433f0c0c29760c36340c97f7308957'),
    'brindille/model/fr.dict.bin': (BINAIRE, '7e87af1778069123739bcd07158b2b0241854c69b33334c98cd051841a039b53'),
    'brindille/model/tts.params.bin': (BINAIRE, '6ddcbc28093ac11ac2bf462da20014a9feb1583fd23c8bb62a8a16ae0fe07c07'),
    'brindille/model/tts.json': (JSON, 'bd048d8ac8b7299115b66fddef0730e9f5d52a9ccb74a69e0150fc90df7baf2a'),
    'brindille/LICENSE': (TEXTE, 'cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30'),
    'brindille/NOTICE': (TEXTE, '4e211d8cafc54570eb2ff639e4489bcc519ce1aa3c836cf1c07ee0d84f12450d'),
    'alkove/index.js': (JS, '794fca0cdc21904edc6ac6322806e8fc6fadf74c1c95dc8b3f3d64c5b507e2f4'),
    'alkove/api-63K47HT3.js': (JS, 'ea0b3ba95141776237c4b5bc7dd6461ed508b0538acbd5c704a48ccf5f54f952'),
    'alkove/chunk-FC4ZSMUX.js': (JS, '77f85a50ed63cbbf58bd57c0a9f271a8ec7a8245ed2b7033e5dcfa847e4da7a6'),
    'alkove/chunk-IMUNEOSF.js': (JS, 'aea9080a4c1f3b605556cca8a26c207e1ab241ef0a605f70cfa9bd048b221cb7'),
    'alkove/chunk-MZOMHWYI.js': (JS, 'ec8440162de9b830b17ea22c54f652aee1f9ebedfe168dce6a5d2bb090ddece2'),
    'alkove/alkove-worker.js': (JS, '5571e7951af0b572732d05a6762b5ff1ec78fc0c934b21111d19072c110bc0d4'),
    'alkove/alkove-worklet.js': (JS, '2e36782149709789f08cb444db9bcf0bb457bfb7e1ca5f1ffe0df247361ece7c'),
    'alkove/alkove-engine.wasm': (WASM, '8c8966c15deffd6aa86b34f4f3c614271b90f0de0893facaaa082e695c3ffdf9'),
    'alkove/models/alkove-focus-1-preview.alk': (BINAIRE, ALKOVE_MODELES['focus'][3]),
    'alkove/models/alkove-cue-1-preview.alk': (BINAIRE, ALKOVE_MODELES['cue'][3]),
    'alkove/LICENSE': (TEXTE, 'cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30'),
    'alkove/NOTICE': (TEXTE, 'b36aaaf3d1d726d937cfa5d5938cd8947569f719c65fbc00e6702edc238b0620'),
}
SCRIPTS = {'v5694-voix.js': JS, 'v5694-brindille.js': JS, 'v5694-voix-page.js': JS, 'v5694-voix.css': 'text/css'}
BINAIRES = (WASM, BINAIRE, JSON)    # servis en octets, sans jeu de caractères ni traduction des écrans


def statiques():
    """Entrées de ``web440.STATIC`` (adresse → type) : fichiers tiers et scripts du lot."""
    return {**{'/static/' + chemin: kind for chemin, (kind, _) in FICHIERS.items()}, **{'/static/' + nom: kind for nom, kind in SCRIPTS.items()}}


# Chargeur Alkove : modèles, moteur, worker et worklet lus depuis AxiorHub seulement (chemins relatifs au préfixe). Le script du
# navigateur (``v5694-voix.js``) reprend exactement ces chemins ; un test refuse toute adresse absolue ou externe.
ALKOVE_CONFIG = {'modelBaseUrl': '/static/alkove/models/', 'assetBaseUrl': '/static/alkove/',
                 'models': {'engine': '/static/alkove/alkove-engine.wasm', 'focus': '/static/alkove/models/alkove-focus-1-preview.alk',
                            'cue': '/static/alkove/models/alkove-cue-1-preview.alk'}}
# API d'Alkove 0.4.2 employée (consignée : la version 0.x peut changer d'API) : Alkove.configure, createCallInput (dictée),
# createAgentInput (conversation : withTrack, turn), input.track, input.mode, input.ready, input.on('turnend'|'speechstart'|'mode'),
# input.dispose().
ALKOVE_API = ('Alkove.configure', 'createCallInput', 'createAgentInput', 'track', 'mode', 'ready', 'on', 'dispose')


def fournisseur(cfg):
    """Voix choisie ; sans choix enregistré : Brindille (V03). Un choix du cabinet est rendu tel quel, même inconnu (refusé à la synthèse)."""
    return str(((cfg or {}).get('speech568') or {}).get('provider') or DEFAUT)


def repli(cfg):
    valeur = str(((cfg or {}).get('speech568') or {}).get('fallback_provider') or REPLI_DEFAUT)
    return valeur if valeur in REPLIS else REPLI_DEFAUT


def fournisseur_serveur(cfg):
    """Voix que le serveur produit lui-même : la voix choisie, ou la voix de repli quand la voix choisie est celle du navigateur."""
    choisi = fournisseur(cfg)
    return repli(cfg) if choisi == 'brindille' else choisi


def preparer(desk, texte, owner='cabinet', matter=''):
    """Texte découpé pour la lecture. Brindille : développé (V04), audité, lu dans le navigateur ; rien n'est synthétisé ni transmis.
    Autres voix : découpage seul, la synthèse reste celle de ``voice568`` (qui développe le texte des voix locales et audite)."""
    from . import assistant567
    from .lecture5694 import developper, nettoyer, phrases
    from .voice568 import paragraph_pause_ms
    profil = assistant567.profile(desk, owner)
    if not profil['speech_enabled']:
        raise Stop('lecture_vocale_desactivee')
    texte = str(texte or '').strip()
    if not 1 <= len(texte) <= 7000:
        raise Stop('texte_lecture_trop_long_7000_maximum')
    choisi = fournisseur(desk.c)
    try:
        from .i18n5631 import current
        langue = current()
    except Exception:   # noqa: BLE001 — hors requête : français
        langue = 'fr'
    navigateur = choisi == 'brindille'
    if choisi == 'elevenlabs':          # chemin ElevenLabs inchangé : ni développement ni nettoyage côté serveur
        morceaux = phrases(texte)
    else:
        morceaux = phrases(developper(nettoyer(texte)))
    if navigateur:
        desk.audit('synthese_vocale', {'owner': owner, 'provider': 'brindille', 'characters': len(texte), 'external': False, 'browser': True})
    return {'provider': choisi, 'browser': navigateur, 'fallback': repli(desk.c), 'paragraphs': morceaux, 'rate': profil['speech_rate'],
            'pause_ms': paragraph_pause_ms(desk.c, langue), 'characters': len(texte), 'external': choisi == 'elevenlabs',
            'transmitted_text': None, 'matter': str(matter or '')}


def etat(desk, owner='cabinet'):
    """Ce que la page Voix et les scripts ont besoin de savoir, sans secret."""
    from . import assistant567
    profil = assistant567.profile(desk, owner)
    return {'provider': fournisseur(desk.c), 'fallback': repli(desk.c), 'server_provider': fournisseur_serveur(desk.c),
            'rate': profil['speech_rate'], 'speech_enabled': profil['speech_enabled'],
            'voice_isolation': bool(profil.get('voice_isolation', True)), 'stop_on_silence': bool(profil.get('stop_on_silence', True)),
            'brindille_revision': BRINDILLE_REVISION, 'alkove_version': ALKOVE_VERSION}


def fichiers_presents():
    """Fichiers embarqués absents ou altérés (chemins), pour le diagnostic de la mise en service."""
    import hashlib
    from pathlib import Path
    racine = Path(__file__).resolve().parent / 'static'
    manquants = []
    for chemin, (_, empreinte) in FICHIERS.items():
        cible = racine / chemin
        if not cible.is_file() or hashlib.sha256(cible.read_bytes()).hexdigest() != empreinte:
            manquants.append(chemin)
    return manquants
