"""5.6.111 (lot U, B6) : précontrôle du poste avant d'installer ou de lancer la dictée (whisper.cpp) et pour le lecteur/aperçu.

Dit, en clair et avec la marche à suivre, ce qui manque sur CE poste ; ne télécharge ni n'installe aucun composant du système.

- **Moteur de dictée** : présent avec l'application installée (``dictee56110.executable``), sinon la raison connue (serveur, hors paquet…).
- **Lancement du moteur** (bouton « Vérifier ce poste », administrateur) : le moteur est lancé une fois avec ``-h`` (aide, aucun modèle, aucun
  son, une seconde au plus) par ``_lancer_essai`` (remplacée dans les essais : aucun vrai moteur). Code de sortie lu :
  * Windows ``0xC0000135`` (bibliothèque absente au chargement) : composant Microsoft Visual C++ manquant → lien officiel de Microsoft ;
  * Ubuntu ``127`` : bibliothèque ``libgomp1`` introuvable. ``libgomp1`` est une dépendance du paquet du poste : elle n'est JAMAIS
    annoncée absente sans ce code ; le message dit alors que le paquet est incomplet ou que l'application tourne hors paquet.
- **Modèle de dictée** : installé ou non (l'installation reste un clic de l'administrateur dans la carte « Dictée sur ce poste »).
- **Microphone** : autorisé par la page (``Permissions-Policy: microphone=(self)``) ; l'autorisation du navigateur et l'adresse sécurisée
  sont lues par ``static/v56111-precontrole.js`` (``navigator.permissions``, ``isSecureContext``), jamais en ouvrant le micro.
- **Lecteur et aperçu** : conversion PDF des documents Word (LibreOffice, ``portable.which``) ; application Ubuntu : fenêtre WebKitGTK
  (``gir1.2-webkit2-4.1``), vérifiée dans un processus séparé (``_webkit_present``, remplacée dans les essais).

Le dernier résultat du bouton est gardé en mémoire du processus (par dossier d'état), jamais en base : l'affichage n'écrit rien.
Routes ``/api440/m56111/precontrole/etat`` (GET, tous les rôles, sans lancer de processus) et ``…/verifier`` (POST, administrateur).
"""
from datetime import datetime, timezone
from html import escape as e
import json
import os
from pathlib import Path
import subprocess
import sys
import threading

from .common import Stop

PREFIXE = 'm56111/precontrole/'
LECTURES = ('etat',)
ECRITURES = ('verifier',)
CODE_MSVC = 0xC0000135
CODE_BIBLIOTHEQUE = 127
DELAI = 15
MAX_POST = 4000
_DERNIER = {}
_VERROU = threading.Lock()
ETATS = {'ok': 'Prêt', 'manque': 'Manquant', 'a_verifier': 'À vérifier', 'non_concerne': 'Sans objet ici', 'navigateur': 'Vérifié par le navigateur'}
TEXTES = {
    'titre': 'Précontrôle du poste',
    'intro': 'Ce qui manque pour la dictée et pour le lecteur, avant d’installer ou de lancer. Rien n’est téléchargé ni installé automatiquement.',
    'verifier': 'Vérifier ce poste',
    'admin_seul': 'Seul l’administrateur lance la vérification du moteur.',
    'dernier': 'Dernière vérification : %s (UTC).',
    'jamais': 'Moteur pas encore lancé : cliquez sur « Vérifier ce poste ».',
    'moteur': 'Moteur de dictée',
    'lancement': 'Lancement du moteur',
    'modele': 'Modèle de dictée',
    'micro': 'Microphone',
    'convertisseur': 'Aperçu PDF des documents Word',
    'fenetre': 'Fenêtre de l’application (Ubuntu)',
    'moteur_ok': 'Fourni avec cette installation.',
    'lancement_ok': 'Le moteur démarre sur ce poste.',
    'msvc': 'Composant Microsoft Visual C++ absent (code 0xC0000135) : le moteur ne peut pas démarrer.',
    'msvc_1': 'Ouvrez la page officielle de Microsoft (lien ci-dessous).',
    'msvc_2': 'Installez « Microsoft Visual C++ Redistributable » (x64).',
    'msvc_3': 'Revenez ici et cliquez sur « Vérifier ce poste ».',
    'msvc_lien': 'Page de Microsoft : composant Visual C++',
    'gomp': 'Bibliothèque libgomp1 introuvable (code 127) : elle est normalement installée avec le paquet du poste.',
    'gomp_1': 'Installez-la : sudo apt-get install -y libgomp1',
    'gomp_2': 'Si l’application a été lancée hors paquet, installez plutôt le paquet du poste.',
    'gomp_3': 'Revenez ici et cliquez sur « Vérifier ce poste ».',
    'echec': 'Le moteur s’est arrêté avec le code %s.',
    'echec_1': 'Réinstallez l’application du poste, puis vérifiez de nouveau.',
    'delai': 'Le moteur n’a pas répondu dans le délai : il a été arrêté.',
    'gomp_info': 'libgomp1 : fournie par le paquet du poste.',
    'modele_ok': 'Modèle %s installé.',
    'modele_absent': 'Modèle %s non installé.',
    'modele_1': 'Installez-le depuis la carte « Dictée sur ce poste » (clic de l’administrateur ; téléchargement vérifié).',
    'micro_page': 'Autorisé par l’application sur cette adresse ; l’autorisation du navigateur est lue ci-contre.',
    'micro_attente': 'Lecture de l’autorisation du navigateur…',
    'conv_ok': 'LibreOffice trouvé : les documents Word peuvent être convertis en PDF.',
    'conv_absent': 'LibreOffice absent : l’aperçu reste en texte, l’export reste au format Word.',
    'conv_win': 'Installez LibreOffice depuis son site officiel (libreoffice.org), puis rouvrez l’aperçu.',
    'conv_linux': 'Installez-le : sudo apt-get install -y libreoffice-writer',
    'fen_ok': 'WebKitGTK présent : la fenêtre de l’application s’ouvre.',
    'fen_absent': 'WebKitGTK absent : l’application s’ouvre dans le navigateur à la place de sa fenêtre.',
    'fen_1': 'Installez : sudo apt-get install -y python3-gi gir1.2-gtk-3.0 gir1.2-webkit2-4.1',
    'fen_attente': 'Non vérifié : cliquez sur « Vérifier ce poste ».',
    'sans_objet_fenetre': 'Concerne seulement l’application installée sur Ubuntu.',
}
MESSAGES = {'introuvable': 'Élément introuvable.', 'role_insuffisant': 'Réservé à l’administrateur du cabinet.',
            'requete_trop_longue': 'Demande trop longue.', 'origine_ou_csrf_refuse': 'Demande refusée.',
            'verification_en_cours': 'Une vérification est déjà en cours : attendez sa fin.'}
STATUTS = {'introuvable': '404 Not Found', 'role_insuffisant': '403 Forbidden', 'origine_ou_csrf_refuse': '403 Forbidden',
           'requete_trop_longue': '413 Payload Too Large', 'verification_en_cours': '409 Conflict'}


def _windows():
    from . import portable
    return portable.WINDOWS


# ------------------------------------------------------------------------------------------------ sondes (remplacées dans les essais)
def _lancer_essai(args, cwd, delai=DELAI):
    """Lance un programme sans console, sortie jamais lue ; rend le code de retour, ou ``None`` à l'échéance (processus arrêté)."""
    from . import portable
    options = {'cwd': str(cwd), 'stdin': subprocess.DEVNULL, 'stdout': subprocess.DEVNULL, 'stderr': subprocess.DEVNULL, 'env': portable.tool_env()}
    if portable.WINDOWS:
        options['creationflags'] = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
    p = subprocess.Popen(args, **options)
    try:
        return p.wait(timeout=delai)
    except subprocess.TimeoutExpired:
        try:
            p.kill()
            p.wait(timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            pass
        return None


def _webkit_present():
    """Ubuntu : GTK 3 et WebKit2 4.1 chargeables par l'interpréteur de l'application, vérifié dans un processus séparé."""
    code = _lancer_essai([sys.executable, '-c', "import gi; gi.require_version('Gtk', '3.0'); gi.require_version('WebKit2', '4.1')"],
                         Path(sys.executable).parent, 20)
    return code == 0


def _convertisseur():
    from .portable import which
    return which('libreoffice') or which('soffice')


def _mode(config):
    try:
        from .diagnostic5634 import install_mode
        return install_mode(config or {})
    except Exception:   # noqa: BLE001
        return 'serveur'


# ------------------------------------------------------------------------------------------------ contrôles
def _item(cle, etat, message, marche=(), lien=''):
    return {'id': cle, 'libelle': TEXTES[cle], 'etat': etat, 'etat_libelle': ETATS[etat], 'message': message, 'marche': list(marche), 'lien': lien}


def lancement(exe, systeme=None):
    """Lance le moteur (aide seulement) et traduit le code de sortie ; rend l'élément « Lancement du moteur »."""
    from .dictee56110 import VC_REDIST
    systeme = systeme or ('windows' if _windows() else 'linux')
    code = _lancer_essai([exe, '-h'], Path(exe).parent)
    if code is None:
        return _item('lancement', 'a_verifier', TEXTES['delai'], (TEXTES['echec_1'],))
    c = int(code) & 0xFFFFFFFF
    if c == 0:
        return _item('lancement', 'ok', TEXTES['lancement_ok'])
    if systeme == 'windows' and c == CODE_MSVC:
        return _item('lancement', 'manque', TEXTES['msvc'], (TEXTES['msvc_1'], TEXTES['msvc_2'], TEXTES['msvc_3']), VC_REDIST)
    if systeme != 'windows' and c == CODE_BIBLIOTHEQUE:
        return _item('lancement', 'manque', TEXTES['gomp'], (TEXTES['gomp_1'], TEXTES['gomp_2'], TEXTES['gomp_3']))
    return _item('lancement', 'a_verifier', TEXTES['echec'] % ('0x%08X' % c if c > 255 else str(c)), (TEXTES['echec_1'],))


def controles(config, lancer=False, systeme=None):
    """Liste des contrôles du poste. ``lancer`` : le moteur et la fenêtre Ubuntu sont réellement essayés (bouton, administrateur)."""
    from . import dictee56110 as D
    systeme = systeme or ('windows' if _windows() else 'linux')
    state_dir = (config or {}).get('state_dir')
    out = []
    exe = D.executable()
    if exe is None:
        raison = D.raison_absence(config)
        out.append(_item('moteur', 'non_concerne' if raison == 'serveur' else 'manque', D.TEXTES[raison]))
    else:
        out.append(_item('moteur', 'ok', TEXTES['moteur_ok'] + ((' ' + TEXTES['gomp_info']) if systeme != 'windows' else '')))
    dernier = _DERNIER.get(str(state_dir or ''), {})
    if exe is not None:
        if lancer:
            out.append(lancement(exe, systeme))
        elif dernier.get('lancement'):
            out.append(dernier['lancement'])
        else:
            out.append(_item('lancement', 'a_verifier', TEXTES['jamais']))
        r = D.reglages_de(state_dir)
        nom = D.MODELES[r['modele']]['nom']
        if D.modele_installe(state_dir, r['modele']):
            out.append(_item('modele', 'ok', TEXTES['modele_ok'] % nom))
        else:
            out.append(_item('modele', 'manque', TEXTES['modele_absent'] % nom, (TEXTES['modele_1'],)))
    out.append(_item('micro', 'navigateur', TEXTES['micro_page']))
    if _convertisseur():
        out.append(_item('convertisseur', 'ok', TEXTES['conv_ok']))
    else:
        out.append(_item('convertisseur', 'manque', TEXTES['conv_absent'], (TEXTES['conv_win'] if systeme == 'windows' else TEXTES['conv_linux'],)))
    if systeme != 'windows' and _mode(config) == 'poste_linux':
        if lancer:
            out.append(_item('fenetre', 'ok', TEXTES['fen_ok']) if _webkit_present() else _item('fenetre', 'manque', TEXTES['fen_absent'], (TEXTES['fen_1'],)))
        elif dernier.get('fenetre'):
            out.append(dernier['fenetre'])
        else:
            out.append(_item('fenetre', 'a_verifier', TEXTES['fen_attente']))
    else:
        out.append(_item('fenetre', 'non_concerne', TEXTES['sans_objet_fenetre']))
    return out


def verifier(config):
    """Bouton « Vérifier ce poste » : contrôles avec lancement ; résultat gardé en mémoire du processus pour l'affichage suivant."""
    if not _VERROU.acquire(blocking=False):
        raise Stop('verification_en_cours')
    try:
        liste = controles(config, lancer=True)
    finally:
        _VERROU.release()
    garde = {x['id']: x for x in liste if x['id'] in ('lancement', 'fenetre')}
    garde['at'] = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')
    _DERNIER[str((config or {}).get('state_dir') or '')] = garde
    manques = [x for x in liste if x['etat'] == 'manque']
    return {'controles': liste, 'manques': len(manques),
            'message': ('Vérification faite : %d élément(s) manquant(s), détail ci-dessous.' % len(manques)) if manques else 'Vérification faite : rien ne manque.'}


# ------------------------------------------------------------------------------------------------ routes /api440/m56111/precontrole/*
def allowed(role, method, api):
    reste = str(api or '').split('?', 1)[0]
    reste = reste[len(PREFIXE):] if reste.startswith(PREFIXE) else ''
    if method in ('GET', 'HEAD') and reste in LECTURES:
        return role in ('administrateur', 'avocat', 'assistant')
    return method == 'POST' and reste in ECRITURES and role == 'administrateur'


def _json(data, status='200 OK'):
    return {'status': status, 'kind': 'application/json; charset=utf-8', 'body': json.dumps(data, ensure_ascii=False),
            'headers': [('Cache-Control', 'private, no-store')]}


def api(env, desk, auth, prefix, name, args, method):
    from .web567 import actor
    _owner, role = actor(env)
    reste = str(name).split('?', 1)[0][len(PREFIXE):].strip('/')
    if reste in LECTURES and method == 'GET':
        if not allowed(role, method, PREFIXE + reste):
            return _json({'error': 'role_insuffisant', 'message': MESSAGES['role_insuffisant']}, STATUTS['role_insuffisant'])
        return _json({'controles': controles(desk.c)})
    if reste not in ECRITURES or method != 'POST':
        return _json({'error': 'introuvable', 'message': MESSAGES['introuvable']}, STATUTS['introuvable'])
    if not allowed(role, method, PREFIXE + reste):
        return _json({'error': 'role_insuffisant', 'message': MESSAGES['role_insuffisant']}, STATUTS['role_insuffisant'])
    try:
        if int(env.get('CONTENT_LENGTH') or 0) > MAX_POST:
            raise Stop('requete_trop_longue')
        from .web440 import check_post
        check_post(env, auth)
        return _json(verifier(desk.c))
    except Stop as ex:
        code = str(ex).split(':')[0]
        return _json({'error': code, 'message': MESSAGES.get(code, code.replace('_', ' '))}, STATUTS.get(code, '400 Bad Request'))


# ------------------------------------------------------------------------------------------------ présentation (Paramètres › Voix)
def section_html(desk, prefix, role):
    """Carte « Précontrôle du poste », au-dessus de « Dictée sur ce poste ». Aucune écriture, aucun processus lancé à l'affichage."""
    config = desk.c
    liste = controles(config)
    dernier = _DERNIER.get(str(config.get('state_dir') or ''), {})
    lignes = []
    for x in liste:
        marche = ''.join('<li>%s</li>' % e(m) for m in x['marche'])
        lien = ('<p><a href="%s" target="_blank" rel="noopener noreferrer">%s</a></p>' % (e(x['lien'], quote=True), e(TEXTES['msvc_lien']))) if x['lien'] else ''
        micro = ('<p class="u111-pc-message" data-u111-micro-etat role="status" aria-live="polite">%s</p>' % e(TEXTES['micro_attente'])) if x['id'] == 'micro' else ''
        lignes.append('<li class="u111-pc u111-pc-%s" data-u111-pc="%s"><span class="u111-pc-nom">%s</span> <span class="u111-pc-etat">%s</span>'
                      '<p class="u111-pc-message">%s</p>%s%s%s</li>' % (
                          e(x['etat'], quote=True), e(x['id'], quote=True), e(x['libelle']), e(x['etat_libelle']), e(x['message']), micro,
                          ('<ol class="u111-pc-marche">%s</ol>' % marche) if marche else '', lien))
    pied = '<p class="ax-muted">%s</p>' % e((TEXTES['dernier'] % dernier['at']) if dernier.get('at') else TEXTES['jamais'])
    if role == 'administrateur':
        pied += ('<form class="m5-form d51-inline" data-api="m56111/precontrole/verifier" data-reload><button class="ax-btn" type="submit">%s</button>'
                 '<p role="status" class="m5-status"></p></form>') % e(TEXTES['verifier'])
    else:
        pied += '<p class="ax-muted">%s</p>' % e(TEXTES['admin_seul'])
    return ('<section class="ax-card u111-precontrole" id="precontrole56111" aria-labelledby="u111-pc-titre"><h2 id="u111-pc-titre">%s</h2><p>%s</p>'
            '<ul class="u111-pc-liste">%s</ul>%s<script defer src="%s"></script></section>') % (
        e(TEXTES['titre']), e(TEXTES['intro']), ''.join(lignes), pied, e(prefix + '/static/v56111-precontrole.js', quote=True))
