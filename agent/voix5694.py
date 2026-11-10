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

5.6.96 (lot voix) :
- V01 : ``preparer`` ne tronque plus rien en silence : le texte entier est normalisé une fois puis découpé en segments de
  ``PLAFOND_SEGMENT`` caractères au plus (phrases, puis mots ; Brindille accepte 2 000 caractères par appel). Au-delà du plafond explicite
  de lecture totale, la coupe se fait à une fin de phrase et ``limite`` l'annonce ; le texte affiché reste entier.
- V04 : ``dernier_repli`` remonte le passage du serveur à sa voix de secours (``voice568:last_fallback``), avec sa cause, jamais le texte.
- V05 : profils de la voix Brindille (``PROFILS``, liste fermée) : ``posee`` (« Français posé », défaut décidé par le cabinet le
  9 novembre 2026 pour les comptes qui n'ont jamais choisi) et ``originale`` (valeurs neutres). Le serveur transmet l'identifiant autorisé
  et ses quatre valeurs ; le worker du navigateur les applique avant la synthèse. Aucun second modèle, aucun calcul serveur.
- V06 : portraits de l'avatar 2D (``PORTRAITS``), dessinés dans ``v5696-avatar.js`` ; module déclaré seulement s'il est choisi.
"""
import re

from .common import Stop

FOURNISSEURS = ('brindille', 'kokoro', 'espeak', 'chatterbox', 'elevenlabs')
SERVEUR = ('espeak', 'kokoro', 'chatterbox', 'elevenlabs')
REPLIS = ('espeak', 'kokoro', 'chatterbox')            # voix de repli : toujours locale (jamais ElevenLabs, service externe)
DEFAUT = 'brindille'
REPLI_DEFAUT = 'espeak'

# 5.6.96 (V01) : plafonds annoncés, jamais silencieux
PLAFOND_SEGMENT = 240          # caractères par segment envoyé au moteur (``lecture5694.phrases``) ; Brindille accepte 2 000 par appel
PLAFOND_LECTURE = 100_000      # plafond explicite d'une lecture totale (annoncé à l'écran)
PLAFOND_EXTERNE = 7_000        # voix externe payante (ElevenLabs) : plafond plus bas, annoncé lui aussi
# 5.6.96 (V05) : hauteur (demi-tons), étendue d'intonation (1 = celle du modèle), aigus (dB), souffle (dB) : ``Tts.voice`` de Brindille.
# « posee » est un point de départ à écouter (cahier v3) : un réglage de la voix Estelle, pas une autre voix.
PROFILS = {
    'posee': {'pitch': -2, 'intonation': 0.9, 'treble': -2, 'breath': -1},
    'originale': {'pitch': 0, 'intonation': 1, 'treble': 0, 'breath': 0},
}
PROFIL_DEFAUT = 'posee'        # décision du cabinet (9 novembre 2026) : installations neuves et comptes qui n'ont jamais choisi
PORTRAITS = ('portrait_a', 'portrait_b')   # V06 : avatars 2D ; toute autre valeur d'``assistant567.avatar`` : aucun portrait

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
SCRIPTS = {'v5694-voix.js': JS, 'v5694-brindille.js': JS, 'v5694-voix-page.js': JS, 'v5694-voix.css': 'text/css',
           'v5696-avatar.js': JS}   # 5.6.96 (V06) : portraits vectoriels ; déclaré dans une page seulement si un portrait est choisi
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


def profil_voix(profil, demande=None):
    """5.6.96 (V05) : identifiant de profil Brindille autorisé : celui demandé (essai d'écoute) s'il est dans la liste fermée, sinon celui
    du compte (``assistant567.profile`` rend déjà le défaut du moment pour un compte qui n'a jamais choisi)."""
    if demande is not None:
        if demande not in PROFILS:
            raise Stop('profil_voix_invalide')
        return demande
    valeur = str((profil or {}).get('voix_brindille') or PROFIL_DEFAUT)
    return valeur if valeur in PROFILS else PROFIL_DEFAUT


def couper_au_plafond(texte, plafond):
    """5.6.96 (V01) : texte ramené sous ``plafond`` à une fin de phrase (sinon à un espace) ; rend (texte, limite) où ``limite`` décrit la
    coupe pour l'annoncer, ou None si rien n'est coupé."""
    if len(texte) <= plafond:
        return texte, None
    fin = max((m.end() for m in re.finditer(r'[.!?…]\s', texte[:plafond + 1])), default=0)
    if fin < plafond // 2:
        fin = texte.rfind(' ', 0, plafond)
    if fin <= 0:
        fin = plafond
    return texte[:fin].rstrip(), {'plafond': plafond, 'caracteres': len(texte), 'lus': len(texte[:fin].rstrip())}


def preparer(desk, texte, owner='cabinet', matter='', profil=None):
    """Texte découpé pour la lecture. Brindille : développé (V04), audité, lu dans le navigateur ; rien n'est synthétisé ni transmis.
    Autres voix : découpage seul, la synthèse reste celle de ``voice568`` (qui développe le texte des voix locales et audite).
    5.6.96 (V01) : texte entier, segments de ``PLAFOND_SEGMENT`` au plus ; au-delà du plafond explicite, ``limite`` annonce la coupe.
    5.6.96 (V05) : ``profil`` (essai d'écoute, liste fermée) ou profil du compte ; ses quatre valeurs partent au worker du navigateur."""
    from . import assistant567
    from .lecture5694 import developper, nettoyer, phrases
    from .voice568 import paragraph_pause_ms
    compte = assistant567.profile(desk, owner)
    if not compte['speech_enabled']:
        raise Stop('lecture_vocale_desactivee')
    identifiant = profil_voix(compte, profil)
    texte = str(texte or '').strip()
    if not texte:
        raise Stop('texte_lecture_vide')
    choisi = fournisseur(desk.c)
    externe = choisi == 'elevenlabs'
    texte, limite = couper_au_plafond(texte, PLAFOND_EXTERNE if externe else PLAFOND_LECTURE)
    try:
        from .i18n5631 import current
        langue = current()
    except Exception:   # noqa: BLE001 — hors requête : français
        langue = 'fr'
    navigateur = choisi == 'brindille'
    if externe:                         # chemin ElevenLabs inchangé : ni développement ni nettoyage côté serveur
        morceaux = phrases(texte, PLAFOND_SEGMENT)
    else:
        morceaux = phrases(developper(nettoyer(texte)), PLAFOND_SEGMENT)
    if navigateur:
        desk.audit('synthese_vocale', {'owner': owner, 'provider': 'brindille', 'characters': len(texte), 'external': False, 'browser': True,
                                       'profil': identifiant})
    return {'provider': choisi, 'browser': navigateur, 'fallback': repli(desk.c), 'paragraphs': morceaux, 'rate': compte['speech_rate'],
            'pause_ms': paragraph_pause_ms(desk.c, langue), 'characters': limite['caracteres'] if limite else len(texte), 'external': externe,
            'transmitted_text': None, 'matter': str(matter or ''), 'segments': sum(len(p) for p in morceaux), 'limite': limite,
            'plafond_segment': PLAFOND_SEGMENT, 'profil': identifiant, 'voix': dict(PROFILS[identifiant])}


def dernier_repli(desk, owner='cabinet'):
    """5.6.96 (V04) : dernier passage à la voix de secours du serveur (``voice568.speech``), avec une cause lisible ; jamais le texte lu."""
    valeur = desk.settings('voice568:last_fallback:' + owner, None)
    if not isinstance(valeur, dict) or not valeur.get('at'):
        return None
    raison = str(valeur.get('reason') or '')
    try:
        from .erreurs5634 import explain
        message = explain(raison.split(':')[0]) or raison
    except Exception:   # noqa: BLE001 — la cause brute reste lisible
        message = raison
    return {'at': str(valeur['at']), 'reason': raison, 'message': message, 'provider': str(valeur.get('provider') or REPLI_DEFAUT),
            'requested': str(valeur.get('requested') or '')}


def etat(desk, owner='cabinet'):
    """Ce que la page Voix et les scripts ont besoin de savoir, sans secret."""
    from . import assistant567
    profil = assistant567.profile(desk, owner)
    avatar = str(profil.get('avatar') or 'icone')
    return {'provider': fournisseur(desk.c), 'fallback': repli(desk.c), 'server_provider': fournisseur_serveur(desk.c),
            'rate': profil['speech_rate'], 'speech_enabled': profil['speech_enabled'],
            'voice_isolation': bool(profil.get('voice_isolation', True)), 'stop_on_silence': bool(profil.get('stop_on_silence', True)),
            'brindille_revision': BRINDILLE_REVISION, 'alkove_version': ALKOVE_VERSION,
            # 5.6.96 : V03 (interruption par choix), V05 (profil Brindille), V06 (avatar), V04 (repli du serveur), V01 (plafonds annoncés)
            'interruption_vocale': profil.get('interruption_vocale') is True, 'profil': profil_voix(profil), 'profils': list(PROFILS),
            'avatar': avatar if avatar in PORTRAITS else 'aucun', 'last_fallback': dernier_repli(desk, owner),
            'plafond': PLAFOND_LECTURE, 'plafond_externe': PLAFOND_EXTERNE}


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
