"""5.6.97 (lot documents-mission) : routes ``/api440/m5689/creation|demandes|portefeuille/*``, pages « Nouveau document », « Document »,
« Demandes » et les deux listes d'Aujourd'hui (§ 13, § 16, § 17).

Le compte et le rôle viennent de la requête authentifiée, jamais du corps. Chaque écriture passe par ``check_post`` (origine + jeton CSRF), porte
une clé d'idempotence, et la version lue quand l'objet en a une (409 sinon, avec un message lisible). Un objet invisible pour la personne reçoit
la même réponse qu'un objet absent (404). Les fichiers volumineux (import, réimport, convention signée) arrivent en octets bruts, après le même
contrôle d'origine et de jeton que ``check_post``. Aucune de ces routes n'appelle un modèle : « Préparer avec l'IA » ne fait qu'inscrire une demande.
"""
import base64
from html import escape as e
import hmac
from pathlib import PurePosixPath
from urllib.parse import quote, urlencode
import uuid

from . import acces5697
from .common import Stop

ROLES = ('administrateur', 'avocat', 'assistant')
LECTURES = ('creation/options', 'creation/document', 'creation/comparer', 'creation/fichier', 'demandes/liste', 'demandes/prefill', 'demandes/lire',
            'demandes/apercu', 'demandes/fichier', 'portefeuille/listes', 'portefeuille/etat', 'portefeuille/candidats')
ECRITURES = ('creation/creer', 'creation/importer', 'creation/enregistrer', 'creation/reimporter', 'creation/copie', 'creation/comparer',
             'demandes/creer', 'demandes/modifier', 'demandes/conflits', 'demandes/convention', 'demandes/deposer', 'portefeuille/definir',
             'portefeuille/action', 'portefeuille/relance')
VALIDATIONS = ('demandes/decider', 'demandes/convertir')        # avocat ou administrateur, droit « valider »
REGLAGES = ('portefeuille/seuil',)                               # administrateur
BINAIRES = ('creation/importer', 'creation/reimporter', 'demandes/deposer')
MAX_BINAIRE = 15_000_000
MESSAGES = {'introuvable': 'Élément introuvable ou non accessible.', 'version_en_conflit': 'Cet élément a été modifié entre-temps : rechargez la page.',
            'cle_idempotence_deja_utilisee': 'Cette action a déjà servi pour une autre demande : rechargez la page.', 'role_insuffisant': 'Action réservée.',
            'origine_ou_csrf_refuse': 'Session expirée : rechargez la page.', 'cle_idempotence_requise': 'Rechargez la page avant de recommencer.',
            'mode_inconnu': 'Choisissez comment créer le document.', 'type_document_invalide': 'Type de document inconnu.',
            'modele_a_choisir': 'Choisissez un modèle du cabinet.', 'modele_retire': 'Ce modèle a été retiré : il ne peut plus être utilisé.',
            'pieces_a_choisir': 'Cochez au moins une pièce.', 'piece_inconnue': 'Une pièce choisie n’existe plus : rechargez la page.',
            'format_non_accepte': 'Format non accepté (Word, OpenDocument, PDF, texte, tableur, image).',
            'contenu_ne_correspond_pas_au_format': 'Le contenu du fichier ne correspond pas à son extension.',
            'fichier_vide_ou_trop_volumineux': 'Fichier vide ou trop volumineux (15 Mo au plus).', 'consigne_trop_courte': 'Décrivez ce que l’IA doit préparer (8 caractères au moins).',
            'type_sans_preparation_ia': 'Ce document se prépare à la main (bordereau : à partir des pièces).',
            'edition_simple_word_seulement': 'L’éditeur simple ne modifie que les documents Word : téléchargez puis réimportez.',
            'modifie_dans_le_stockage': 'Le fichier a été modifié dans le stockage : rechargez pour le relire.',
            'relecture_differente_a_verifier': 'Le fichier relu ne correspond pas à ce qui a été envoyé : à vérifier.',
            'texte_de_version_non_conserve': 'Le texte de cette version n’est pas conservé pour la comparaison.',
            'demande_close': 'Cette demande est close.', 'demande_non_acceptee': 'La demande doit d’abord être acceptée.',
            'controle_conflits_a_refaire': 'Les parties ont changé : relancez la vérification des conflits avant de décider.',
            'motif_decision_conflit_requis': 'Un rapprochement ou une limite subsiste : indiquez votre motif (10 caractères au moins).',
            'motif_refus_requis': 'Indiquez le motif du refus (10 caractères au moins).', 'aucune_partie': 'Indiquez au moins le client ou une partie.',
            'role_partie_invalide': 'Rôle de partie inconnu.', 'responsable_inconnu': 'Responsable inconnu.', 'origine_inconnue': 'Origine inconnue.',
            'personne_sans_acces_au_dossier': 'Cette personne n’a pas accès au dossier.', 'prochaine_action_terminee': 'Cette action est terminée : choisissez-en une autre.',
            'prochaine_action_autre_dossier': 'Cette action appartient à un autre dossier.', 'prochaine_action_introuvable': 'Action introuvable.',
            'motif_attente_requis': 'Indiquez ce qui est attendu.', 'aucune_attente': 'Aucune attente en cours pour ce dossier.',
            'titre_action_requis': 'Indiquez l’action à mener.', 'version_perimee': 'Le dossier a été modifié entre-temps : rechargez la page.',
            'racine_dossiers_absente': 'Aucune racine de dossiers n’est configurée.', 'reference_deja_utilisee': 'Cette référence est déjà utilisée.',
            'champs_invalides': 'Certains champs sont invalides.', 'json_requis': 'Requête invalide.', 'requete_trop_longue': 'Fichier trop volumineux.'}
STATUTS = {'introuvable': '404 Not Found', 'route_inconnue': '404 Not Found', 'role_insuffisant': '403 Forbidden', 'origine_ou_csrf_refuse': '403 Forbidden',
           'version_en_conflit': '409 Conflict', 'version_perimee': '409 Conflict', 'cle_idempotence_deja_utilisee': '409 Conflict', 'modifie_dans_le_stockage': '409 Conflict',
           'reference_deja_utilisee': '409 Conflict', 'champs_invalides': '422 Unprocessable Entity'}


def allowed(role, route):
    route = route.split('?', 1)[0]
    if role not in ROLES:
        return False
    if route in VALIDATIONS:
        return role in ('administrateur', 'avocat')
    if route in REGLAGES:
        return role == 'administrateur'
    return route in LECTURES or route in ECRITURES


def droit_requis(route):
    """Droit réglable en jeu (``membres5650.category``) : décider et ouvrir le dossier = valider ; seuil = réglage du cabinet."""
    route = route.split('?', 1)[0]
    if route in VALIDATIONS:
        return 'valider'
    if route in REGLAGES:
        return 'reglages'
    return None


def _erreur(ex):
    from .web440 import json_out
    code = str(ex).split(':')[0]
    detail = getattr(ex, 'detail', None) or {}
    message = detail.get('message') if isinstance(detail, dict) and detail.get('message') else None
    if not message:
        from .erreurs5634 import explain
        message = MESSAGES.get(code) or explain(code, detail if isinstance(detail, dict) else None)
    return json_out({'error': code, 'message': message, 'detail': detail if isinstance(detail, dict) else {}}, STATUTS.get(code, '400 Bad Request'))


def api(env, desk, auth, prefix, name, args, method):
    try:
        return _api(env, desk, auth, prefix, name, args, method)
    except Stop as ex:
        return _erreur(ex)


def _binaire(env, auth):
    """Octets bruts d'un import : mêmes contrôles d'origine et de jeton que ``check_post``, taille bornée avant lecture."""
    if env.get('HTTP_ORIGIN') != auth['origin'] or not hmac.compare_digest(env.get('HTTP_X_CSRF_TOKEN', ''), auth['csrf']):
        raise Stop('origine_ou_csrf_refuse')
    try:
        longueur = int(env.get('CONTENT_LENGTH', '0') or 0)
    except ValueError:
        longueur = 0
    if longueur <= 0 or longueur > MAX_BINAIRE:
        raise Stop('fichier_vide_ou_trop_volumineux')
    if env.get('CONTENT_TYPE', '').split(';')[0] != 'application/octet-stream':
        raise Stop('json_requis')
    return env['wsgi.input'].read(longueur)


def _fichier(nom, mime, octets):
    from .livrables_export5689 import _disposition
    return {'status': '200 OK', 'kind': mime or 'application/octet-stream', 'body': octets,
            'headers': [('Content-Disposition', _disposition(nom)), ('Cache-Control', 'private, no-store'), ('X-Content-Type-Options', 'nosniff')]}


def _api(env, desk, auth, prefix, name, args, method):
    from . import documents5697 as docs, mission5697 as mis, portefeuille5697 as pf
    from .web440 import check_post, json_out
    from .web567 import actor
    owner, role = actor(env)
    a = acces5697.Acteur(owner, role)
    route = name[len('m5689/'):].split('?', 1)[0]
    if not allowed(role, route):
        raise Stop('role_insuffisant' if route in VALIDATIONS + REGLAGES else 'route_inconnue')
    if method == 'GET':
        if route not in LECTURES:
            raise Stop('route_inconnue')
        g = lambda k: str(args.get(k) or '')
        if route == 'creation/options':
            return json_out(docs.options(desk, a, g('dossier')))
        if route == 'creation/document':
            return json_out(docs.ouvrir(desk, a, g('id')))
        if route == 'creation/comparer':
            return json_out(docs.comparer(desk, a, g('id'), int(g('de') or 1), int(g('a')) if g('a') else None))
        if route == 'creation/fichier':
            return _fichier(*docs.fichier(desk, a, g('id')))
        if route == 'demandes/liste':
            return json_out({'demandes': mis.lister(desk, a, [x for x in g('etats').split(',') if x in mis.ETATS] or None)})
        if route == 'demandes/prefill':
            return json_out(mis.preremplir(desk, a, g('origine'), g('ref')))
        if route == 'demandes/lire':
            return json_out(mis.lire(desk, a, g('id')))
        if route == 'demandes/apercu':
            return json_out(mis.apercu(desk, a, g('id')))
        if route == 'demandes/fichier':
            return _fichier(*mis.preparation(desk, a, g('id'), g('piece')))
        if route == 'portefeuille/listes':
            return json_out(pf.listes(desk, a, g('mes') == '1'))
        if route == 'portefeuille/etat':
            return json_out(pf.etat(desk, a, g('dossier')))
        return json_out({'candidats': pf.candidats(desk, a, g('dossier'))})
    if method != 'POST':
        raise Stop('route_inconnue')
    if route in BINAIRES:
        octets = _binaire(env, auth)
        g = lambda k: str(args.get(k) or '')
        if route == 'creation/importer':
            return json_out(docs.creer(desk, a, {'dossier': g('dossier'), 'mode': 'import', 'type': g('type') or 'libre', 'titre': g('titre'),
                                                 'nom_fichier': g('nom'), 'octets': octets}, g('cle')))
        if route == 'creation/reimporter':
            return json_out(docs.enregistrer(desk, a, g('id'), g('version'), g('cle'), octets=octets))
        return json_out(mis.deposer(desk, a, g('id'), g('nature'), g('nom'), octets, g('mime') or 'application/octet-stream'))
    data = check_post(env, auth)
    s = lambda k: str(data.get(k) or '')
    if route == 'creation/creer':
        if data.get('mode') == 'import' and data.get('contenu'):
            data['octets'] = base64.b64decode(str(data['contenu']), validate=False)
        return json_out(docs.creer(desk, a, data, s('cle')))
    if route == 'creation/enregistrer':
        return json_out(docs.enregistrer(desk, a, s('id'), data.get('version'), s('cle'), lignes=_lignes(data)))
    if route == 'creation/copie':
        return json_out(docs.copie(desk, a, s('id'), _lignes(data), s('cle')))
    if route == 'creation/comparer':
        return json_out(docs.comparer(desk, a, s('id'), int(data.get('de') or 1), None, _lignes(data)))
    if route == 'demandes/creer':
        return json_out(mis.creer(desk, a, _demande(data), s('cle')))
    if route == 'demandes/modifier':
        return json_out(mis.modifier(desk, a, s('id'), data.get('version'), _demande(data)))
    if route == 'demandes/conflits':
        return json_out(mis.verifier_conflits(desk, a, s('id'), data.get('version')))
    if route == 'demandes/decider':
        return json_out(mis.decider(desk, a, s('id'), data.get('version'), s('decision'), s('motif')))
    if route == 'demandes/convention':
        return json_out(mis.preparer_convention(desk, a, s('id')))
    if route == 'demandes/convertir':
        return json_out(mis.convertir(desk, a, s('id'), data.get('version'), {'reference': s('reference')}))
    if route == 'portefeuille/definir':
        champs = {k: data[k] for k in ('responsable_id', 'etat_operatoire', 'next_action', 'attente_de', 'motif_attente', 'attente_depuis', 'revoir_le') if k in data}
        return json_out(pf.definir(desk, a, s('dossier'), champs, data.get('version'), s('cle')))
    if route == 'portefeuille/action':
        return json_out(pf.ajouter_action(desk, a, s('dossier'), s('titre'), s('echeance'), data.get('version'), s('cle')))
    if route == 'portefeuille/relance':
        return json_out(pf.preparer_relance(desk, a, s('dossier'), s('cle')))
    if route == 'portefeuille/seuil':
        return json_out(pf.definir_seuil(desk, a, data.get('jours')))
    raise Stop('route_inconnue')


def _lignes(data):
    valeur = data.get('lignes')
    if isinstance(valeur, list):
        return [str(x) for x in valeur]
    return str(valeur or '').replace('\r\n', '\n').split('\n')


def _demande(data):
    """Champs du formulaire de demande : parties en lignes (``partie_nom_N``, ``partie_role_N``) ou en liste ; honoraires à plat."""
    out = {k: data[k] for k in ('origine', 'source_ref', 'source_date', 'correspondant', 'correspondant_role', 'objet', 'client_id', 'client_nom',
                                'client_etat', 'responsable', 'type_mission') if k in data}
    if isinstance(data.get('parties'), list):
        out['parties'] = data['parties']
    elif any(k.startswith('partie_nom_') for k in data):
        out['parties'] = [{'nom': data.get('partie_nom_%d' % i), 'role': data.get('partie_role_%d' % i) or 'adverse', 'courriel': data.get('partie_courriel_%d' % i)}
                          for i in range(12) if str(data.get('partie_nom_%d' % i) or '').strip()]
    if any(k.startswith('honoraires_') for k in data):
        out['honoraires'] = {k[len('honoraires_'):]: data[k] for k in data if k.startswith('honoraires_')}
    return out


# ------------------------------------------------------------------------------------------------ pages
def _cle():
    return 'u' + uuid.uuid4().hex


def _lien(prefix, chemin, **params):
    params = {k: v for k, v in params.items() if v not in (None, '')}
    return e(prefix + chemin + (('?' + urlencode(params)) if params else ''), quote=True)


def lien_nouveau_document(prefix, dossier=''):
    """Même bouton depuis Aujourd'hui, Produire et Dossiers (§ 13)."""
    return '<a class="ax-btn m97-nouveau-doc" href="%s">+ Nouveau document</a>' % _lien(prefix, '/nouveau-document', dossier=dossier)


def lien_nouvelle_demande(prefix, origine='saisie', ref=''):
    """Même formulaire depuis Courriels (« Créer une demande depuis ce message »), Agenda (rendez-vous) et Dossiers (appel ou saisie)."""
    libelle = {'courriel': 'Créer une demande depuis ce message', 'rendez_vous': 'Nouvelle demande'}.get(origine, 'Nouvelle demande')
    return '<a class="ax-btn ghost m97-nouvelle-demande" href="%s">%s</a>' % (_lien(prefix, '/demandes', nouvelle='1', origine=origine, ref=ref), e(libelle))


def boutons_html(prefix, dossier=''):
    """Barre commune « + Nouveau document » / « Nouvelle demande » (Aujourd'hui, Produire, fiche du dossier)."""
    return '<p class="m97-boutons">%s %s</p>' % (lien_nouveau_document(prefix, dossier), lien_nouvelle_demande(prefix, 'saisie'))


def _statut():
    return '<p class="m97-statut" role="status" aria-live="polite" data-m97-statut></p>'


def _options(choix, courant=''):
    return ''.join('<option value="%s"%s>%s</option>' % (e(k, quote=True), ' selected' if k == courant else '', e(v)) for k, v in choix)


def _dossiers_visibles(desk, a):
    from .common import load_matters, matter_display
    perimetre = acces5697.dossiers_autorises(desk, a)
    return sorted(((m['id'], matter_display(m)) for m in load_matters(desk.c, unfiltered=True) if m['id'] in perimetre), key=lambda x: x[1].lower())


def page_nouveau_document(desk, a, prefix, args):
    """Panneau « Nouveau document » : dossier prérempli, type, titre et rangement proposés, quatre départs."""
    from . import documents5697 as docs
    dossier_id = str(args.get('dossier') or '')
    dossiers = _dossiers_visibles(desk, a)
    if not dossier_id or dossier_id not in dict(dossiers):
        return ('<section class="m97" aria-labelledby="m97-titre"><h1 id="m97-titre">Nouveau document</h1><form method="get" class="m97-choix-dossier">'
                '<label for="m97-dossier">Dossier</label><select id="m97-dossier" name="dossier" required>%s</select><button class="ax-btn">Continuer</button></form></section>') % (
            _options([('', 'Choisir un dossier')] + dossiers))
    o = docs.options(desk, a, dossier_id)
    types = ''.join('<option value="%s" data-m97-rappel="%s" data-m97-ia="%s">%s</option>' % (e(t['id'], quote=True), e(t['rappel'], quote=True), '1' if t['ia'] else '',
                                                                                             e(t['libelle'])) for t in o['types'])
    champs = ''.join('<fieldset class="m97-champs" data-m97-type="%s"><legend>Données à reprendre — %s</legend>%s</fieldset>' % (
        e(t['id'], quote=True), e(t['libelle']), ''.join('<label>%s<input name="valeurs.%s" autocomplete="off"></label>' % (e(c['libelle']), e(c['cle'], quote=True))
                                                          for c in t['champs'])) for t in o['types'] if t['champs'])
    modeles = _options([('', 'Choisir un modèle')] + [(m['id'], '%s — %s' % (m['categorie'], m['titre']) if m['categorie'] else m['titre']) for m in o['modeles']])
    pieces = ''.join('<label class="m97-piece"><input type="checkbox" name="pieces" value="%s"> Pièce n° %s — %s (version %s)</label>' % (
        e(p['id'], quote=True), e(p['numero']), e(p['titre'] or 'sans intitulé'), e(str(p['version']))) for p in o['pieces']) or '<p>Aucune pièce active dans ce dossier.</p>'
    ia = ('<p class="m97-avis" role="note">L’IA est en pause : la demande sera enregistrée et préparée à la reprise. Les autres choix restent disponibles.</p>'
          if o['ia_en_pause'] else '<p class="m97-aide">La demande rejoint la file de rédaction ; le projet apparaîtra dans « À relire ». Rien n’est envoyé.</p>')
    cle = _cle()
    return ('<section class="m97" id="m97-nouveau" aria-labelledby="m97-titre"><header class="m97-tete"><h1 id="m97-titre">Nouveau document</h1>'
            '<p class="m97-dossier">Dossier : <strong>%s</strong> · <a href="%s">changer</a></p></header>'
            '<form class="m97-form" data-m97-api="creation/creer" data-m97-suite="/document?id={document_id}" data-m97-cle="%s">'
            '<input type="hidden" name="dossier" value="%s">'
            '<fieldset class="m97-modes"><legend>Comment commencer ?</legend>'
            '<label class="m97-mode"><input type="radio" name="mode" value="vierge" checked><span><strong>Document vierge</strong> Structure du type choisi, champs à compléter visibles.</span></label>'
            '<label class="m97-mode"><input type="radio" name="mode" value="modele"><span><strong>Modèle du cabinet</strong> Modèle versionné de la bibliothèque.</span></label>'
            '<label class="m97-mode"><input type="radio" name="mode" value="import"><span><strong>Importer un fichier</strong> Word, PDF, OpenDocument, texte ou image.</span></label>'
            '<label class="m97-mode"><input type="radio" name="mode" value="ia"><span><strong>Préparer avec l’IA</strong> Une demande explicite, à relire ensuite.</span></label></fieldset>'
            '<div class="m97-grille"><label for="m97-type">Type proposé</label><select id="m97-type" name="type" data-m97-types>%s</select>'
            '<label for="m97-titre-doc">Titre</label><input id="m97-titre-doc" name="titre" maxlength="160" placeholder="Proposé à partir du type et du dossier" autocomplete="off"></div>'
            '<p class="m97-aide" data-m97-rappel-texte></p>'
            '<div class="m97-section" data-m97-mode="vierge">%s<fieldset class="m97-champs" data-m97-type="bordereau"><legend>Pièces du bordereau</legend>%s</fieldset></div>'
            '<div class="m97-section" data-m97-mode="modele"><label for="m97-modele">Modèle</label><select id="m97-modele" name="modele">%s</select>'
            '<p class="m97-aide">Un modèle retiré ne peut plus être utilisé ; un modèle non validé reste utilisable et signalé.</p></div>'
            '<div class="m97-section" data-m97-mode="import"><label for="m97-fichier">Fichier</label><input id="m97-fichier" type="file" name="fichier" '
            'accept=".docx,.odt,.pdf,.txt,.md,.csv,.xlsx,.ods,.png,.jpg,.jpeg"></div>'
            '<div class="m97-section" data-m97-mode="ia"><label for="m97-consigne">Consigne</label><textarea id="m97-consigne" name="consigne" rows="4" maxlength="4000"></textarea>%s</div>'
            '<p class="m97-actions"><button class="ax-btn" type="submit">Créer le document</button> <a class="ax-btn ghost" href="%s">Annuler</a></p>%s</form>'
            '<p class="m97-aide">Le document est rangé dans le sous-dossier du type (Correspondances, Procédure, Projets…) s’il existe, sinon à la racine du dossier. '
            'Il rejoint le registre commun : Aujourd’hui, Produire et le dossier montrent la même version.</p></section>') % (
        e(o['dossier']['libelle']), _lien(prefix, '/nouveau-document'), cle, e(dossier_id, quote=True), types, champs, pieces, modeles, ia,
        _lien(prefix, '/dossier', id=dossier_id), _statut())


def page_document(desk, a, prefix, args):
    """Fiche d'un document : version courante, éditeur simple, comparaison, historique, téléchargement et réimport."""
    from . import documents5697 as docs
    ident = str(args.get('id') or '')
    d = docs.ouvrir(desk, a, ident)
    comparaison = ''
    if args.get('de'):
        diff = docs.comparer(desk, a, ident, int(args.get('de')), int(args.get('a') or d['version']))
        comparaison = _diff_html(diff, 'Version %s → version %s' % (diff['de'], diff['a']))
    versions = ''.join('<li>%s · version %d · %s%s%s</li>' % (
        e(_jour(v['le'])), v['version'], e(v['auteur'] or '—'), ' · courante' if v['courante'] else '',
        (' · <a href="%s">comparer à la courante</a>' % _lien(prefix, '/document', id=ident, de=v['version'], a=d['version'])) if (v['texte_garde'] and not v['courante']) else '')
        for v in d['versions'])
    editeur = ''
    if d['editeur_cabinet']:
        editeur = '<a class="ax-btn ghost" href="%s">Ouvrir dans l’éditeur du cabinet</a>' % _lien(prefix, '/documents/edit', path=d['chemin'], matter=d['dossier_id'])
    if d['editable']:
        edition = ('<form class="m97-form m97-editeur" data-m97-api="creation/enregistrer" data-m97-suite="/document?id={document_id}" data-m97-cle="%s" data-m97-conflit>'
                   '<input type="hidden" name="id" value="%s"><input type="hidden" name="version" value="%d">'
                   '<label for="m97-texte">Texte du document (un paragraphe par ligne)</label>'
                   '<textarea id="m97-texte" name="lignes" rows="18" spellcheck="true" data-no-dictation>%s</textarea>'
                   '<p class="m97-aide">Seuls les paragraphes modifiés sont réécrits : styles, numérotation, tableaux, en-têtes et pieds de page restent ceux du fichier.</p>'
                   '<p class="m97-actions"><button class="ax-btn" type="submit">Enregistrer la version %d</button> '
                   '<button class="ax-btn ghost" type="button" data-m97-comparer>Comparer avec la version enregistrée</button></p>%s'
                   '<div class="m97-conflit" data-m97-conflit-actions hidden><button class="ax-btn ghost" type="button" data-m97-copie>Enregistrer mon texte comme copie</button> '
                   '<a class="ax-btn ghost" href="%s">Recharger la dernière version</a></div><div data-m97-diff></div></form>') % (
            _cle(), e(ident, quote=True), d['version'], e('\n'.join(d['paragraphes'])), d['version'] + 1, _statut(), _lien(prefix, '/document', id=ident))
    else:
        raison = {'ok': 'Ce format ne s’édite pas ici.', 'modifie_dans_le_stockage': 'Le fichier a été modifié dans le stockage depuis la dernière version connue.'}.get(
            d['lecture'], 'Le fichier n’a pas pu être lu (%s).' % d['lecture'])
        edition = '<p class="m97-avis" role="note">%s Téléchargez-le, modifiez-le, puis réimportez-le : une nouvelle version sera créée.</p>' % e(raison)
    reimport = ('<form class="m97-form" data-m97-api="creation/reimporter" data-m97-binaire data-m97-suite="/document?id={document_id}" data-m97-cle="%s">'
                '<input type="hidden" name="id" value="%s"><input type="hidden" name="version" value="%d"><label for="m97-reimport">Réimporter une version modifiée (%s)</label>'
                '<input id="m97-reimport" type="file" name="fichier" accept=".%s" required> <button class="ax-btn ghost" type="submit">Réimporter</button>%s</form>') % (
        _cle(), e(ident, quote=True), d['version'], e(d['format']), e(d['format'], quote=True), _statut())
    return ('<section class="m97" id="m97-document" aria-labelledby="m97-titre"><header class="m97-tete"><h1 id="m97-titre">%s</h1>'
            '<p class="m97-meta"><span>Version %d</span><span>%s</span><span>Par %s</span><span>%s</span><span>%s</span></p>'
            '<p class="m97-actions"><a class="ax-btn ghost" href="%s">Télécharger</a> %s <a class="ax-btn ghost" href="%s">Retour au dossier</a></p></header>'
            '%s%s%s<section class="m97-versions" aria-labelledby="m97-t-versions"><h2 id="m97-t-versions">Versions</h2><ul>%s</ul></section></section>') % (
        e(d['titre']), d['version'], e(_jour(d['le'])), e(d['auteur'] or '—'), e(d['type']), e(PurePosixPath(d['chemin']).name),
        e(prefix + '/api440/m5689/creation/fichier?id=' + quote(ident), quote=True), editeur, _lien(prefix, '/dossier', id=d['dossier_id']),
        comparaison, edition, reimport, versions)


def _jour(valeur):
    valeur = str(valeur or '')
    return '%s/%s/%s' % (valeur[8:10], valeur[5:7], valeur[:4]) if len(valeur) >= 10 and valeur[4] == '-' else valeur


def _diff_html(diff, titre):
    lignes = ''.join('<li class="m97-%s"><span class="m97-signe">%s</span> %s</li>' % (x['op'], {'ajout': 'Ajouté :', 'retrait': 'Retiré :', 'egal': ''}[x['op']], e(x['texte']))
                     for x in diff['lignes'] if x['op'] != 'egal' or len(diff['lignes']) < 60)
    return '<section class="m97-diff" aria-label="%s"><h2>%s · %d changement(s)</h2><ul>%s</ul></section>' % (e(titre, quote=True), e(titre), diff['changements'], lignes)


def page_demandes(desk, a, prefix, args):
    from . import mission5697 as mis
    if args.get('id'):
        return page_demande(desk, a, prefix, mis.lire(desk, a, str(args['id'])))
    nouvelle = ''
    if args.get('nouvelle'):
        origine = str(args.get('origine') or 'saisie')
        base = mis.preremplir(desk, a, origine, str(args.get('ref') or ''))
        nouvelle = ('<section class="m97-carte" aria-labelledby="m97-t-nouvelle"><h2 id="m97-t-nouvelle">Nouvelle demande</h2>'
                    '<form class="m97-form" data-m97-api="demandes/creer" data-m97-suite="/demandes?id={id}" data-m97-cle="%s">'
                    '<input type="hidden" name="source_ref" value="%s"><div class="m97-grille">'
                    '<label for="m97-origine">Origine</label><select id="m97-origine" name="origine">%s</select>'
                    '<label for="m97-date">Date</label><input id="m97-date" name="source_date" type="date" value="%s">'
                    '<label for="m97-corr">Correspondant</label><input id="m97-corr" name="correspondant" value="%s" autocomplete="off">'
                    '<label for="m97-role">Rôle du correspondant</label><select id="m97-role" name="correspondant_role">%s</select>'
                    '<label for="m97-objet">Objet</label><input id="m97-objet" name="objet" value="%s" autocomplete="off"></div>'
                    '<p class="m97-aide">L’expéditeur ou l’appelant n’est pas le client par défaut : indiquez son rôle, puis le client à l’étape suivante.</p>'
                    '<p class="m97-actions"><button class="ax-btn" type="submit">Créer la demande</button></p>%s</form></section>') % (
            _cle(), e(base['source_ref'], quote=True), _options([(k, v) for k, v in mis.ORIGINES.items() if k == origine or (not base['source_ref'] and k in ('appel', 'saisie'))], origine),
            e(base['source_date'], quote=True), e(base['correspondant'], quote=True), _options(list(mis.ROLES_CORRESPONDANT.items()), ''), e(base['objet'], quote=True), _statut())
    lignes = ''.join('<tr><th scope="row"><a href="%s">%s</a></th><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>' % (
        _lien(prefix, '/demandes', id=d['id']), e(d['objet'] or 'Sans objet'), e(d['client_nom'] or 'À identifier'), e(d['origine']), e(d['etat_libelle']), e(_jour(d['modifie_le'])))
        for d in mis.lister(desk, a))
    tableau = ('<table class="m97-table"><caption class="sr-only">Demandes</caption><thead><tr><th scope="col">Objet</th><th scope="col">Client</th><th scope="col">Origine</th>'
               '<th scope="col">État</th><th scope="col">Modifiée le</th></tr></thead><tbody>%s</tbody></table>' % lignes) if lignes else '<p>Aucune demande pour le moment.</p>'
    return ('<section class="m97" id="m97-demandes" aria-labelledby="m97-titre"><header class="m97-tete"><h1 id="m97-titre">Demandes de mission</h1>'
            '<p class="m97-actions">%s %s</p></header>%s%s</section>') % (
        lien_nouvelle_demande(prefix, 'appel').replace('Nouvelle demande', 'Nouvelle demande (appel)'), lien_nouvelle_demande(prefix, 'saisie'), nouvelle, tableau)


def page_demande(desk, a, prefix, d):
    from . import mission5697 as mis
    final = d['etat'] in mis.FINALS
    parties = list(d['parties']) + [{'nom': '', 'role': 'adverse', 'courriel': ''}] * 3
    lignes_parties = ''.join('<tr><td><label class="sr-only" for="m97-pn%d">Nom</label><input id="m97-pn%d" name="partie_nom_%d" value="%s"></td>'
                             '<td><label class="sr-only" for="m97-pr%d">Rôle</label><select id="m97-pr%d" name="partie_role_%d">%s</select></td>'
                             '<td><label class="sr-only" for="m97-pc%d">Courriel</label><input id="m97-pc%d" name="partie_courriel_%d" type="email" value="%s"></td></tr>' % (
                                 i, i, i, e(p['nom'], quote=True), i, i, i, _options(list(mis.ROLES_PARTIES.items()), p['role']), i, i, i, e(p['courriel'], quote=True))
                             for i, p in enumerate(parties[:12]))
    h = d['honoraires'] or {}

    def euros(c):
        return '' if c is None else ('%d,%02d' % (c // 100, c % 100))
    qualification = ('<section class="m97-carte" aria-labelledby="m97-t-qualif"><h2 id="m97-t-qualif">Qualifier</h2>'
                     '<form class="m97-form" data-m97-api="demandes/modifier" data-m97-cle="%s"><input type="hidden" name="id" value="%s"><input type="hidden" name="version" value="%d">'
                     '<fieldset%s><div class="m97-grille">'
                     '<label for="m97-cn">Client</label><input id="m97-cn" name="client_nom" value="%s" list="m97-doublons" autocomplete="off">'
                     '<label for="m97-ce">Client existant ou à créer</label><select id="m97-ce" name="client_etat">%s</select>'
                     '<label for="m97-ob">Objet</label><input id="m97-ob" name="objet" value="%s">'
                     '<label for="m97-co">Correspondant</label><input id="m97-co" name="correspondant" value="%s">'
                     '<label for="m97-cr">Rôle du correspondant</label><select id="m97-cr" name="correspondant_role">%s</select>'
                     '<label for="m97-re">Personne responsable</label><input id="m97-re" name="responsable" value="%s" autocomplete="off">'
                     '<label for="m97-ty">Type de mission</label><select id="m97-ty" name="type_mission">%s</select></div>'
                     '<table class="m97-parties"><caption>Parties et rôles</caption><thead><tr><th scope="col">Nom</th><th scope="col">Rôle</th><th scope="col">Courriel</th></tr></thead>'
                     '<tbody>%s</tbody></table>'
                     '<fieldset class="m97-honoraires"><legend>Honoraires (devis et convention)</legend><div class="m97-grille">'
                     '<label for="m97-hm">Mode</label><select id="m97-hm" name="honoraires_mode">%s</select>'
                     '<label for="m97-hf">Forfait HT (€)</label><input id="m97-hf" name="honoraires_forfait" inputmode="decimal" value="%s">'
                     '<label for="m97-ht">Taux horaire HT (€)</label><input id="m97-ht" name="honoraires_taux_horaire" inputmode="decimal" value="%s">'
                     '<label for="m97-hp">Provision (€)</label><input id="m97-hp" name="honoraires_provision" inputmode="decimal" value="%s">'
                     '<label for="m97-hpe">Périmètre</label><input id="m97-hpe" name="honoraires_perimetre" value="%s"></div></fieldset>'
                     '<p class="m97-actions"><button class="ax-btn" type="submit">Enregistrer</button></p></fieldset>%s</form>'
                     '<datalist id="m97-doublons">%s</datalist></section>') % (
        _cle(), e(d['id'], quote=True), d['version'], ' disabled' if final else '', e(d['client_nom'], quote=True), _options(list(mis.CLIENT_ETATS.items()), d['client_etat']),
        e(d['objet'], quote=True), e(d['correspondant'], quote=True), _options(list(mis.ROLES_CORRESPONDANT.items()), d['correspondant_role']), e(d['responsable'], quote=True),
        _options(list(mis.TYPES_MISSION.items()), d['type_mission']), lignes_parties,
        _options([('', 'À préciser'), ('forfait', 'Forfait'), ('temps', 'Temps passé'), ('mixte', 'Forfait et temps')], h.get('mode', '')),
        euros(h.get('forfait_cents')), euros(h.get('taux_horaire_cents')), euros(h.get('provision_cents')), e(h.get('perimetre', ''), quote=True), _statut(),
        ''.join('<option value="%s">' % e(x['libelle'], quote=True) for x in d['doublons']))
    doublons = ''
    if d['doublons']:
        doublons = '<div class="m97-avis" role="note"><p><strong>Doublons possibles</strong> (à vérifier, rien n’est fusionné) :</p><ul>%s</ul></div>' % ''.join(
            '<li>%s : %s</li>' % (e({'demande': 'Demande', 'dossier': 'Dossier', 'contact': 'Contact'}[x['nature']]), e(x['libelle'])) for x in d['doublons'])
    manquants = ('<div class="m97-avis" role="note"><p><strong>Éléments manquants :</strong> %s</p></div>' % e(', '.join(d['manquants']))) if d['manquants'] else ''
    c = d['controle']
    if c:
        rapprochements = ''.join('<li><strong>%s</strong> — %s : %s (%s, %s)</li>' % (e(r['party']), e(r['matter_label'] or ''), e(r['other_name']), e(r['level']), e(r['risk']))
                                 for r in c['rapprochements'])
        masques = ('<p class="m97-alerte" role="alert">%d rapprochement(s) dans un dossier auquel vous n’avez pas accès : faites examiner la demande par un membre autorisé.</p>' % c['masques']) if c['masques'] else ''
        controle = ('<div class="m97-controle%s"><p><strong>%s</strong> %s</p><p>Contrôle du %s, parties version %d%s.</p>%s<ul>%s</ul>'
                    '<details><summary>Couverture et limites</summary><ul>%s</ul><p>%s</p></details></div>') % (
            ' m97-perime' if not c['valide'] else '', 'Contrôle valide.' if c['valide'] else 'Contrôle périmé : les parties ont changé, relancez la vérification.',
            e(c['resume']), e(_jour(c['le'])), c['parties_version'], (' · décision : %s (%s)' % (e(c['decision']), e(c['motif']))) if c['decision'] else '',
            masques, rapprochements or '<li>Aucun rapprochement visible.</li>', ''.join('<li>%s</li>' % e(x) for x in c['limites']), e(c['methode']))
    else:
        controle = '<p>Aucun contrôle pour le moment.</p>'
    conflits = ('<section class="m97-carte" aria-labelledby="m97-t-conflits"><h2 id="m97-t-conflits">Vérifier les conflits</h2>%s'
                '<form class="m97-form" data-m97-api="demandes/conflits" data-m97-cle="%s"><input type="hidden" name="id" value="%s"><input type="hidden" name="version" value="%d">'
                '<button class="ax-btn ghost" type="submit"%s>Lancer la vérification</button>%s</form></section>') % (
        controle, _cle(), e(d['id'], quote=True), d['version'], ' disabled' if final else '', _statut())
    decision = ''
    if a.role in ('administrateur', 'avocat') and not final:
        decision = ('<section class="m97-carte" aria-labelledby="m97-t-decider"><h2 id="m97-t-decider">Décider</h2>'
                    '<form class="m97-form" data-m97-api="demandes/decider" data-m97-cle="%s"><input type="hidden" name="id" value="%s"><input type="hidden" name="version" value="%d">'
                    '<label for="m97-motif">Motif (auteur et date enregistrés)</label><textarea id="m97-motif" name="motif" rows="2" maxlength="500"></textarea>'
                    '<p class="m97-actions"><button class="ax-btn" type="submit" name="decision" value="accepter">Accepter la mission</button> '
                    '<button class="ax-btn ghost" type="submit" name="decision" value="refuser">Refuser</button></p>%s</form></section>') % (
            _cle(), e(d['id'], quote=True), d['version'], _statut())
    preps = ''.join('<li>%s : <a href="%s">%s</a> · empreinte %s%s</li>' % (
        e(mis.NATURES_PREPARATION.get(p['nature'], p['nature'])), e(prefix + '/' + _url_prep(d['id'], p['id']), quote=True), e(p['nom']), e(p['sha256'][:12]),
        ' · rangé dans le dossier' if p['document_id'] else '') for p in d['preparations'])
    convention = ('<section class="m97-carte" aria-labelledby="m97-t-conv"><h2 id="m97-t-conv">Devis et convention</h2><p>État : <strong>%s</strong></p><ul>%s</ul>'
                  '<form class="m97-form" data-m97-api="demandes/convention" data-m97-cle="%s"><input type="hidden" name="id" value="%s">'
                  '<button class="ax-btn ghost" type="submit">Préparer le projet de convention</button>%s</form>'
                  '<form class="m97-form" data-m97-api="demandes/deposer" data-m97-binaire data-m97-cle="%s"><input type="hidden" name="id" value="%s">'
                  '<label for="m97-nat">Document</label><select id="m97-nat" name="nature">%s</select>'
                  '<label for="m97-sig">Fichier</label><input id="m97-sig" type="file" name="fichier" required> <button class="ax-btn ghost" type="submit">Déposer</button>%s</form>'
                  '<p class="m97-aide">Le statut « signée » exige le fichier signé ; il est gardé tel quel, avec son empreinte.</p></section>') % (
        e({'signee': 'Signée', 'projet': 'Projet préparé'}.get(d['convention_etat'], 'Aucune')), preps or '<li>Aucun document.</li>', _cle(), e(d['id'], quote=True), _statut(),
        _cle(), e(d['id'], quote=True), _options([('convention_signee', 'Convention signée'), ('justificatif', 'Justificatif')]), _statut())
    ouverture = ''
    if d['etat'] == 'acceptee' and a.role in ('administrateur', 'avocat'):
        ap = mis.apercu(desk, a, d['id'])
        ouverture = ('<section class="m97-carte" aria-labelledby="m97-t-ouvrir"><h2 id="m97-t-ouvrir">Ouvrir le dossier</h2><dl class="m97-apercu">'
                     '<dt>Futur dossier</dt><dd>%s — %s</dd><dt>Rangement</dt><dd>%s</dd><dt>Client</dt><dd>%s</dd><dt>Parties</dt><dd>%s</dd><dt>Responsable</dt><dd>%s</dd>'
                     '<dt>Documents repris</dt><dd>%s</dd></dl>%s'
                     '<form class="m97-form" data-m97-api="demandes/convertir" data-m97-suite="/dossier?id={dossier_id}" data-m97-cle="%s"><input type="hidden" name="id" value="%s">'
                     '<input type="hidden" name="version" value="%d"><label for="m97-ref">Référence (facultatif)</label><input id="m97-ref" name="reference" placeholder="%s" maxlength="40">'
                     '<p class="m97-actions"><button class="ax-btn" type="submit">Ouvrir le dossier</button></p>%s</form></section>') % (
            e(ap['reference']), e(ap['titre']), e(ap['emplacement']), e(ap['client'] or 'À identifier'), e(', '.join('%s (%s)' % (p['nom'], p['role']) for p in ap['parties']) or '—'),
            e(ap['responsable'] or '—'), e(', '.join(ap['documents']) or 'aucun'),
            ('<div class="m97-avis" role="note"><p><strong>Restent à régulariser :</strong> %s</p></div>' % e(', '.join(ap['a_regulariser']))) if ap['a_regulariser'] else '',
            _cle(), e(d['id'], quote=True), d['version'], e(ap['reference'], quote=True), _statut())
    if d.get('dossier_id'):
        ouverture = '<p class="m97-ok" role="status">Dossier ouvert : <a href="%s">voir le dossier</a>.</p>' % _lien(prefix, '/dossier', id=d['dossier_id'])
    journal = ''.join('<li>%s · %s · %s %s</li>' % (e(_jour(j['le'])), e(j['acteur']), e(j['evenement']), e(j['detail'])) for j in d['journal'])
    return ('<section class="m97" id="m97-demande" aria-labelledby="m97-titre" data-m97-demande="%s"><header class="m97-tete"><h1 id="m97-titre">Demande : %s</h1>'
            '<p class="m97-meta"><span class="m97-etat m97-etat-%s">%s</span><span>%s du %s</span><span>Version %d</span></p>'
            '<p><a href="%s">Toutes les demandes</a></p></header>%s%s%s%s%s%s%s'
            '<details class="m97-journal"><summary>Historique</summary><ul>%s</ul></details></section>') % (
        e(d['id'], quote=True), e(d['objet'] or 'sans objet'), e(d['etat'], quote=True), e(d['etat_libelle']), e(d['origine_libelle']), e(_jour(d['source_date'])), d['version'],
        _lien(prefix, '/demandes'), manquants, doublons, qualification, conflits, decision, convention, ouverture, journal)


def _url_prep(demande, piece):
    return 'api440/m5689/demandes/fichier?' + urlencode({'id': demande, 'piece': piece})


# ------------------------------------------------------------------------------------------------ Aujourd'hui (§ 17)
def aujourdhui_html(desk, prefix, owner, role, mes=False):
    """Deux listes ciblées sous le rang d'Aujourd'hui : nombre, filtre « Mes dossiers », motif et bouton d'action. Rien si aucune affaire active."""
    from . import portefeuille5697 as pf
    a = acces5697.Acteur(owner, role)
    l = pf.listes(desk, a, mes)
    if not l['sans_action']['nombre'] and not l['attentes']['nombre'] and not mes:     # rien à suivre : seulement les boutons communs
        return '<section class="c530-card m97-aujourdhui" id="m97-suivi" aria-label="Documents et demandes">%s</section>' % boutons_html(prefix)

    def liste(cle, titre, vide, bouton):
        bloc = l[cle]
        items = ''.join('<li><a class="m97-ligne" href="%s">%s</a><span class="m97-motif">%s</span>%s</li>' % (
            _lien(prefix, '/dossier', id=x['dossier_id']), e(x['libelle']), e(x['motif']), bouton(x)) for x in bloc['elements'])
        suite = ('<p class="m97-suite">%d autre(s) : <a href="%s">voir les dossiers</a></p>' % (bloc['nombre'] - len(bloc['elements']), _lien(prefix, '/dossiers', mes='1' if mes else ''))
                 if bloc['nombre'] > len(bloc['elements']) else '')
        return ('<section class="m97-liste" aria-labelledby="m97-t-%s"><h3 id="m97-t-%s">%s <span class="m97-nombre">%d</span></h3>%s%s</section>') % (
            cle, cle, e(titre), bloc['nombre'], ('<ul>%s</ul>' % items) if items else '<p class="m97-vide">%s</p>' % e(vide), suite)

    def ajouter(x):
        choisir = ''
        if x.get('suggestion'):
            choisir = ('<form class="m97-form m97-inline" data-m97-api="portefeuille/definir" data-m97-cle="%s"><input type="hidden" name="dossier" value="%s">'
                       '<input type="hidden" name="version" value="%d"><input type="hidden" name="next_action" value="%s">'
                       '<button class="ax-btn ghost" type="submit">Choisir : %s</button>%s</form>') % (
                _cle(), e(x['dossier_id'], quote=True), x['version'], e(x['suggestion']['ref'], quote=True),
                e('%s%s' % (x['suggestion']['libelle'][:60], (' (%s)' % _jour(x['suggestion']['date'])) if x['suggestion'].get('date') else '')), _statut())
        return choisir + ('<form class="m97-form m97-inline" data-m97-api="portefeuille/action" data-m97-cle="%s"><input type="hidden" name="dossier" value="%s">'
                          '<input type="hidden" name="version" value="%d"><label class="sr-only" for="m97-a-%s">Prochaine action pour %s</label>'
                          '<input id="m97-a-%s" name="titre" placeholder="Ajouter une prochaine action" required maxlength="200">'
                          '<button class="ax-btn ghost" type="submit">Ajouter</button>%s</form>') % (
            _cle(), e(x['dossier_id'], quote=True), x['version'], e(x['dossier_id'], quote=True), e(x['libelle'], quote=True), e(x['dossier_id'], quote=True), _statut())

    def relancer(x):
        return ('<form class="m97-form m97-inline" data-m97-api="portefeuille/relance" data-m97-cle="%s"><input type="hidden" name="dossier" value="%s">'
                '<button class="ax-btn ghost" type="submit">Préparer une relance</button>%s</form>') % (_cle(), e(x['dossier_id'], quote=True), _statut())
    filtre = ('<p class="m97-filtre"><a href="%s"%s>Tous les dossiers accessibles</a> · <a href="%s"%s>Mes dossiers</a></p>') % (
        _lien(prefix, '/aujourdhui'), '' if mes else ' aria-current="true"', _lien(prefix, '/aujourdhui', mes='1'), ' aria-current="true"' if mes else '')
    return ('<section class="c530-card m97-aujourdhui" id="m97-suivi" aria-labelledby="m97-t-suivi"><h2 id="m97-t-suivi">Suivi des dossiers</h2>%s%s<div class="m97-deux">%s%s</div>'
            '<script defer src="%s"></script></section>') % (
        boutons_html(prefix), filtre, liste('sans_action', 'Dossiers sans prochaine action', 'Chaque dossier actif a une prochaine action.', ajouter),
        liste('attentes', 'Attentes à relancer', 'Aucune attente à relancer aujourd’hui.', relancer), e(prefix + '/static/v5697-mission.js', quote=True))


# ------------------------------------------------------------------------------------------------ routage des pages
PAGES = ('/nouveau-document', '/document', '/demandes')


def page_route(env, cfg, auth, prefix, path, args, shell):
    """Pages authentifiées ; le contrôle d'accès est refait par chaque service avant tout rendu (404 neutre)."""
    from .desk import Desk
    from .web440 import page_out
    from .web567 import actor
    owner, role = actor(env)
    a = acces5697.Acteur(owner, role)
    desk = Desk(cfg)
    titres = {'/nouveau-document': 'Nouveau document', '/document': 'Document', '/demandes': 'Demandes de mission'}
    head = '<script defer src="%s/static/v5697-mission.js"></script>' % e(prefix, quote=True)
    try:
        try:
            if path == '/nouveau-document':
                corps = page_nouveau_document(desk, a, prefix, args)
            elif path == '/document':
                corps = page_document(desk, a, prefix, args)
            else:
                corps = page_demandes(desk, a, prefix, args)
            out = page_out(shell(titres[path], corps, prefix, auth['csrf'], '/dossiers' if path != '/demandes' else '/dossiers', head))
        except Stop as ex:
            code = str(ex).split(':')[0]
            out = page_out(shell(titres[path], '<p class="notice">%s</p><p><a class="ax-btn ghost" href="%s">Retour aux dossiers</a></p>' % (
                e(MESSAGES.get(code) or code), _lien(prefix, '/dossiers')), prefix, auth['csrf'], '/dossiers'))
            out['status'] = STATUTS.get(code, '400 Bad Request')
        out.setdefault('headers', []).append(('Cache-Control', 'private, no-store'))
        return out
    finally:
        try:
            desk.db.close()
        except Exception:   # noqa: BLE001
            pass
