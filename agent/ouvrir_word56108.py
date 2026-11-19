"""5.6.108 (lot W) : « Ouvrir dans Word / LibreOffice » (.docx) et « Ouvrir avec l'application du poste » (.pdf : Acrobat, PDF Chisel ou tout
éditeur installé) sur un poste (Windows 11 : application associée à l'extension ; Ubuntu 24.04 : ``xdg-open``, ou LibreOffice pour un .docx).
Mêmes garanties pour les deux formats ; pour un PDF, le contrôle compare le nombre de pages et le texte de chaque page.

- **Postes seulement** (``fichiers56103.poste``) : sur le serveur, AxiorHub ne peut pas lancer un logiciel sur l'ordinateur de l'avocat ; la page
  le dit et propose les autres moyens. Aucun traitement de texte installé : message clair, rien n'est lancé.
- **Copie de travail, jamais l'original** : la séance (``wordcraft56108.ouvrir_seance``, mêmes droits, même 404 neutre) copie le document dans le
  dossier privé de l'application (``<état>/editeur56108/<séance>/travail/<nom>.docx``, droits 0700/0600) ; c'est cette copie qui est ouverte.
- **Surveillance par empreinte** : ``etat`` relit la copie (lecture seule) et compare son SHA-256 à celui de l'ouverture ; la page interroge cet
  état et propose « Reprendre cette version » quand un enregistrement est constaté.
- **Reprise comme nouvelle version** : ``reprendre`` vérifie que la copie est toujours celle montrée (409 sinon), puis passe par le même contrôle
  après enregistrement et la même décision que WordCraft (``proposer`` puis ``decider`` : fichier voisin, création exclusive, relu ; l'original
  n'est jamais remplacé, stockage local ``LocalFolder`` ou Nextcloud par la source de la page « Fichiers »).
- **Nettoyage** : la copie de travail est effacée après « Garder » (séance close) ou « Abandonner la copie de travail » ; un fichier encore tenu
  ouvert par le traitement de texte est repris par la maintenance des séances.
"""
import hashlib
from html import escape as e
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys

from .common import Stop
from . import wordcraft56108 as WC

DOCX_MIME = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
MIMES = {'docx': DOCX_MIME, 'pdf': 'application/pdf'}
NOMS = (('word.document', 'Microsoft Word'), ('libreoffice', 'LibreOffice'), ('soffice', 'LibreOffice'), ('writer', 'LibreOffice Writer'),
        ('openoffice', 'OpenOffice'), ('wps', 'WPS Office'), ('onlyoffice', 'ONLYOFFICE'), ('acrord', 'Adobe Acrobat Reader'),
        ('acrobat', 'Adobe Acrobat'), ('pdfchisel', 'PDF Chisel'), ('pdf chisel', 'PDF Chisel'), ('foxit', 'Foxit PDF'), ('sumatra', 'SumatraPDF'),
        ('msedge', 'Microsoft Edge'), ('appxd4nrz8ff68srnhf9t5a8sbjyar1cr723', 'Microsoft Edge'), ('okular', 'Okular'), ('evince', 'Visionneuse de documents'),
        ('xournal', 'Xournal++'), ('masterpdf', 'Master PDF Editor'), ('firefox', 'Firefox'), ('chrome', 'Google Chrome'))
DEFAUTS = {'docx': 'le traitement de texte associé aux documents Word', 'pdf': 'l’application associée aux PDF'}


def disponible(auth):
    from . import fichiers56103 as F
    return bool(F.poste(auth))


def _nom_lisible(valeur, format_='docx'):
    bas = str(valeur or '').lower()
    for cle, nom in NOMS:
        if cle in bas:
            return nom
    return DEFAUTS.get(format_, DEFAUTS['docx'])


def _detecter_windows(format_='docx'):
    try:
        import winreg
    except ImportError:
        return None
    ext = '.' + format_
    progids = []
    try:   # choix de l'utilisateur (Explorateur), puis association du système
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r'Software\Microsoft\Windows\CurrentVersion\Explorer\FileExts\%s\UserChoice' % ext) as k:
            progids.append(winreg.QueryValueEx(k, 'ProgId')[0])
    except OSError:
        pass
    try:
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, ext) as k:
            progids.append(winreg.QueryValueEx(k, '')[0])
    except OSError:
        pass
    for progid in [x for x in progids if x]:
        try:
            with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, progid + r'\shell\open\command') as k:
                commande = winreg.QueryValueEx(k, '')[0]
        except OSError:
            continue
        if commande:
            return {'nom': _nom_lisible(progid + ' ' + commande, format_), 'systeme': 'windows', 'format': format_}
    soffice = shutil.which('soffice') if format_ == 'docx' else None
    return {'nom': 'LibreOffice', 'systeme': 'windows', 'commande': soffice, 'format': format_} if soffice else None


def _detecter_linux(format_='docx'):
    ouvreur = shutil.which('xdg-open')
    if ouvreur and shutil.which('xdg-mime'):
        try:
            sortie = subprocess.run(['xdg-mime', 'query', 'default', MIMES[format_]], capture_output=True, text=True, timeout=5).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            sortie = ''
        if sortie:
            return {'nom': _nom_lisible(sortie, format_), 'systeme': 'linux', 'format': format_}
    if format_ != 'docx':
        return None
    for nom in ('libreoffice', 'soffice'):
        chemin = shutil.which(nom)
        if chemin:
            return {'nom': 'LibreOffice', 'systeme': 'linux', 'commande': chemin, 'format': format_}
    return None


def traitement(format_='docx'):
    """Application installée sur ce poste pour ce format ('docx' ou 'pdf') : {'nom', 'systeme', 'commande'?} ou None (aucune)."""
    if format_ not in MIMES:
        return None
    if sys.platform.startswith('win'):
        return _detecter_windows(format_)
    if sys.platform.startswith('linux'):
        return _detecter_linux(format_)
    return None


def _lancer(chemin, info):
    """Ouvre la copie de travail dans le traitement de texte (jamais l'original). Aucun interpréteur de commandes : liste d'arguments."""
    if info.get('systeme') == 'windows' and not info.get('commande'):
        os.startfile(str(chemin))   # noqa: S606 — application associée aux .docx, sur la copie privée
        return
    commande = [info['commande'], '--writer', str(chemin)] if info.get('commande') and info.get('format', 'docx') == 'docx' else \
        ([info['commande'], str(chemin)] if info.get('commande') else ['xdg-open', str(chemin)])
    subprocess.Popen(commande, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True,
                     start_new_session=not sys.platform.startswith('win'))


# Points d'injection des tests (aucun logiciel lancé, aucun registre lu pendant les tests)
DETECTER = traitement
LANCER = _lancer


def _copie(desk, ident, nom):
    return WC.dossier_seance(desk, ident) / 'travail' / nom


def _nom_sur(nom):
    nom = re.sub(r'[\\/:*?"<>|\x00-\x1f]', '_', str(nom or 'document.docx'))[:180]
    return nom if nom.lower().endswith(('.docx', '.pdf')) else nom + '.docx'


def ouvrir(desk, auth, acteur, ident_source, chemin, sources_=None):
    if not disponible(auth):
        raise Stop('indisponible_sur_serveur')
    format_ = WC.nature(chemin)
    if format_ not in MIMES:
        raise Stop('format_non_pris_en_charge')
    info = DETECTER(format_)
    if not info:
        raise Stop('aucune_application_pdf' if format_ == 'pdf' else 'aucun_traitement_de_texte')
    s = WC.ouvrir_seance(desk, auth, acteur, ident_source, chemin, 'traitement', True, sources_)
    data = (WC.dossier_seance(desk, s['seance']) / 'original.docx').read_bytes()
    copie = _copie(desk, s['seance'], _nom_sur(s['nom']))
    WC._prive(copie.parent)
    WC._ecrire_prive(copie, data)
    desk.db.execute('UPDATE editeur56108_seances SET travail=? WHERE id=?', (copie.name, s['seance']))
    desk.db.commit()
    try:
        LANCER(copie, info)
    except (OSError, ValueError, subprocess.SubprocessError):
        desk.audit('editeur56108_lancement_impossible', {'par': acteur[0], 'chemin': chemin})
        return {**s, 'lance': False, 'traitement': info['nom'], 'copie': str(copie),
                'message': WC.MESSAGES['lancement_impossible'] + ' Copie de travail : ' + str(copie)}
    desk.audit('editeur56108_copie_ouverte_sur_le_poste', {'par': acteur[0], 'chemin': chemin, 'traitement': info['nom']})
    return {**s, 'lance': True, 'traitement': info['nom'], 'copie': str(copie),
            'message': 'Copie de travail ouverte dans %s. Modifiez puis enregistrez (Ctrl+S) : AxiorHub détecte l’enregistrement.' % info['nom']}


def _verrou(copie):
    """Fichier de verrou laissé par Word (« ~$… ») ou LibreOffice (« .~lock.…# ») : le document est encore ouvert."""
    d = copie.parent
    return any((d / n).exists() for n in ('~$' + copie.name[2:], '~$' + copie.name, '.~lock.' + copie.name + '#'))


def etat(desk, auth, acteur, ident, sources_=None):
    """Lecture seule : empreinte actuelle de la copie de travail, comparée à l'ouverture (et au dernier candidat repris)."""
    s = WC.seance(desk, acteur, ident)
    if s['outil'] != 'traitement':
        raise Stop('introuvable')
    WC._source(desk, auth, acteur, s['source'], s['chemin'], sources_)
    copie = _copie(desk, ident, s['travail'] or 'document.docx')
    if not copie.is_file():
        return {'seance': ident, 'present': False, 'modifie': False, 'message': 'La copie de travail n’existe plus (fermée ou nettoyée).'}
    st = copie.stat()
    if st.st_size > WC.MAX_DOCX:
        return {'seance': ident, 'present': True, 'modifie': True, 'trop_volumineux': True, 'message': WC.MESSAGES['document_word_trop_volumineux']}
    sha = hashlib.sha256(copie.read_bytes()).hexdigest()
    deja = sha == s['candidat_sha']
    return {'seance': ident, 'present': True, 'sha256': sha, 'taille': st.st_size, 'modifie': sha != s['sha256'] and not deja,
            'deja_repris': deja, 'ouvert': _verrou(copie), 'revision': s['revision']}


def reprendre(desk, auth, acteur, ident, sha_vu, sources_=None):
    """La copie de travail enregistrée devient un candidat contrôlé (même contrôle que WordCraft). ``sha_vu`` : empreinte montrée (409 sinon)."""
    s = WC.seance(desk, acteur, ident)
    if s['outil'] != 'traitement':
        raise Stop('introuvable')
    copie = _copie(desk, ident, s['travail'] or 'document.docx')
    if not copie.is_file():
        raise Stop('introuvable')
    if copie.stat().st_size > WC.MAX_DOCX:
        raise Stop('document_word_trop_volumineux')
    data = copie.read_bytes()
    sha = hashlib.sha256(data).hexdigest()
    if sha != str(sha_vu or ''):
        raise Stop('version_perimee')
    if sha == s['sha256']:
        raise Stop('aucune_modification')
    return WC.proposer(desk, auth, acteur, ident, s['revision'], data, sources_)


# ------------------------------------------------------------------------------------------------------------------- page
def page(desk, auth, prefix, acteur, args, shell, sources_=None):
    s, p = str(args.get('s') or ''), str(args.get('p') or '')
    format_ = WC.nature(p)
    titre = WC.libelle_traitement(p)
    if acteur[1] not in WC.ROLES_EDITION:
        return WC._page_simple(shell, prefix, auth, titre, '<h1 id="w108-titre">%s</h1><p class="w108-alerte" role="status">%s</p>' % (
            e(titre), e(WC.MESSAGES['role_insuffisant'])), '403 Forbidden')
    if not disponible(auth):
        return WC._page_simple(shell, prefix, auth, titre, (
            '<h1 id="w108-titre">%s</h1><p class="w108-alerte" role="status">Indisponible en version serveur : sur le serveur, AxiorHub ne peut pas '
            'lancer un logiciel sur votre ordinateur. Téléchargez le document depuis la page Fichiers, ou modifiez-le dans l’éditeur de documents '
            '(serveur) ou dans l’éditeur intégré s’il est proposé.</p>') % e(titre))
    try:
        source, chemin, props, analyse, data = WC.lire_pour_page(desk, auth, acteur, s, p, sources_, ('docx', 'pdf'))
    except Stop as ex:
        code = str(ex).split(':')[0]
        if code in ('introuvable', 'dossier_reserve', 'chemin_est_un_dossier'):
            return WC._introuvable(shell, prefix, auth, titre)
        return WC._page_simple(shell, prefix, auth, titre, '<h1 id="w108-titre">%s</h1><p class="w108-alerte" role="status">%s</p>' % (
            e(titre), e(WC.message(code))), WC.STATUTS.get(code, '422 Unprocessable Entity'))
    info = DETECTER(format_)
    nom = PurePosixPath(chemin).name
    if not info and format_ == 'pdf':
        return WC._page_simple(shell, prefix, auth, titre, (
            '<h1 id="w108-titre">%s</h1><p class="w108-doc"><span class="w108-nom">%s</span></p><p class="w108-alerte" role="status">Aucune application '
            'n’est associée aux PDF sur ce poste (Adobe Acrobat, PDF Chisel ou un autre lecteur ou éditeur de PDF) : installez-en une, puis rechargez '
            'cette page. En attendant, l’aperçu en lecture seule reste disponible.</p>') % (e(titre), e(nom)))
    if not info:
        return WC._page_simple(shell, prefix, auth, titre, (
            '<h1 id="w108-titre">%s</h1><p class="w108-doc"><span class="w108-nom">%s</span></p><p class="w108-alerte" role="status">Aucun traitement '
            'de texte n’est installé sur ce poste (ni Microsoft Word, ni LibreOffice) : installez-en un, puis rechargez cette page. En attendant, '
            'l’aperçu en lecture seule reste disponible.</p>') % (e(titre), e(nom)))
    config = {'api': prefix + '/api440/' + WC.PREFIXE, 's': source.ident, 'p': chemin, 'nom': nom, 'traitement': info['nom']}
    corps = ('<h1 id="w108-titre">%s</h1><p class="w108-doc"><span class="w108-nom">%s</span></p>'
             '<p class="w108-note">AxiorHub ouvre une copie de travail dans %s (jamais l’original).</p><p class="w108-note">Quand vous enregistrez '
             'dans cette application, AxiorHub le détecte et vous propose de reprendre cette version : elle est contrôlée puis gardée, si vous '
             'le décidez, comme nouvelle version à côté de l’original.</p>'
             '<p class="w108-actions"><button type="button" class="ax-btn" data-w108="word-ouvrir">Ouvrir une copie de travail dans %s</button></p>'
             '<p class="w108-etat" role="status" aria-live="polite" data-w108-etat></p>'
             '<p class="w108-actions" data-w108-reprise hidden><button type="button" class="ax-btn" data-w108="word-reprendre">Reprendre cette version'
             '</button><button type="button" class="ax-btn ghost" data-w108="fermer">Abandonner la copie de travail</button></p>'
             '<section class="w108-controle" data-w108-controle hidden aria-labelledby="w108-t-controle"><h2 id="w108-t-controle" tabindex="-1">'
             'Contrôle de la version enregistrée</h2><div data-w108-rapport></div><p class="w108-actions">'
             '<button type="button" class="ax-btn" data-w108="garder">Garder cette version</button>'
             '<button type="button" class="ax-btn ghost" data-w108="abandonner">Abandonner cette version</button></p>'
             '<label class="w108-confirmer" data-w108-malgre hidden><input type="checkbox" data-w108-malgre-case> L’original a changé : garder '
             'quand même ma version à côté, sous un nom distinct.</label></section>'
             '<div hidden data-w108-config="%s" data-w108-mode="traitement"></div>') % (
        e(titre), e(nom), e(info['nom']), e(info['nom']), e(json.dumps(config, ensure_ascii=False), quote=True))
    return WC._page_simple(shell, prefix, auth, titre, corps)
