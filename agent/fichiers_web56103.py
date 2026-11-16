"""5.6.103 (lot F) : page « Fichiers » (``GET /fichiers``) et routes ``/api440/m56103/fichiers/…`` (services de ``fichiers56103``).

- Page : deux volets côte à côte, chacun avec ses onglets ; à 390 px, les deux volets deviennent deux onglets (« Volet 1 », « Volet 2 »).
  La liste est chargée en JSON (lecture seule) ; la barre d'actions et les boîtes de dialogue sont rendues ici (texte visible, clavier).
  Interrupteur de la nouvelle interface (``interface56100``) coupé : un seul volet (présentation simple) ; droits, non-effacement et
  lecture seule pendant l'affichage ne dépendent jamais de l'interrupteur.
- Routes : lectures en GET (``lister``, ``proprietes``, ``versions``, ``favoris``) ; recherche et aperçu du renommage en POST sans effet (corps
  JSON) ; écritures en POST avec ``check_post``, corps borné, clé d'idempotence (``cle``), 409 sur version périmée, journal d'audit.
  Réponses privées (``Cache-Control: private, no-store``). Objet absent, hors racines ou dossier réservé : même 404 neutre. Les paramètres
  de chemin s'appellent ``p`` (et ``dossier_cible``) : le service fait lui-même le contrôle des dossiers réservés et répond 404, au lieu du
  refus nominatif du contrôle central (qui dirait qu'un dossier réservé existe).
"""
from html import escape as e
import json

from .common import Stop
from . import fichiers56103 as F

PAGE = '/fichiers'
PREFIXE = 'm56103/fichiers/'
LECTURES = ('lister', 'proprietes', 'versions', 'favoris')
ECRITURES = ('chercher', 'apercu', 'renommer', 'transferer', 'supprimer', 'dossier', 'note', 'favori', 'zip', 'extraire', 'restaurer', 'verrou',
             'poste', 'poste_scruter')
POSTE_SEUL = ('poste', 'poste_scruter')
ROLES_POSTE = ('administrateur', 'avocat')
MAX_POST = 64_000
STATUTS = {'introuvable': '404 Not Found', 'version_perimee': '409 Conflict', 'deja_existant': '409 Conflict', 'cle_reutilisee': '409 Conflict',
           'operation_en_cours': '409 Conflict', 'conflits_renommage': '409 Conflict', 'destination_dans_la_source': '409 Conflict',
           'destination_occupee': '409 Conflict', 'role_insuffisant': '403 Forbidden', 'origine_ou_csrf_refuse': '403 Forbidden',
           'controle_acces_indisponible': '503 Service Unavailable', 'stockage_injoignable': '503 Service Unavailable',
           'requete_trop_longue': '413 Payload Too Large'}
MESSAGES = {
    'introuvable': 'Élément introuvable.',
    'version_perimee': 'Ce fichier a changé depuis l’affichage : rechargez le dossier avant de recommencer. Rien n’a été remplacé.',
    'deja_existant': 'Un élément porte déjà ce nom à cet endroit : rien n’a été remplacé. Choisissez un autre nom.',
    'cle_reutilisee': 'Cette demande a déjà servi pour une autre action : rechargez la page.',
    'operation_en_cours': 'Cette action est déjà en cours ou a été interrompue : rechargez le dossier pour voir ce qui a été fait.',
    'conflits_renommage': 'Le renommage contient des conflits : rien n’a été renommé. Corrigez les lignes signalées dans l’aperçu.',
    'rien_a_renommer': 'Aucun nom ne change.',
    'destination_dans_la_source': 'Un dossier ne peut pas être copié ou déplacé dans lui-même.',
    'destination_occupee': 'Trop d’éléments portent déjà ce nom : renommez d’abord.',
    'deplacement_entre_sources': 'Le déplacement entre Nextcloud et ce poste n’est pas proposé : copiez, puis supprimez l’original vous-même.',
    'copie_dossier_entre_sources': 'Entre Nextcloud et ce poste, seuls les fichiers se copient (pas les dossiers).',
    'racine_non_modifiable': 'Un dossier racine ne se déplace pas et ne se supprime pas d’ici.',
    'corbeille_indisponible': 'La suppression n’est proposée que pour Nextcloud (corbeille restaurable). Rien n’est supprimé sur ce poste.',
    'versions_indisponibles': 'Cette source ne conserve pas de versions.',
    'role_insuffisant': 'Votre rôle ne permet pas cette action.',
    'origine_ou_csrf_refuse': 'Session expirée ou origine refusée : rechargez la page.',
    'controle_acces_indisponible': 'Contrôle des accès indisponible : réessayez dans un instant. Rien n’a été affiché ni modifié.',
    'stockage_injoignable': 'Le stockage ne répond pas : le contenu n’a pas pu être lu (ce n’est pas un dossier vide). Réessayez plus tard.',
    'nextcloud_ou_dossier_local_a_configurer': 'Le stockage des fichiers n’est pas configuré (Paramètres › Nextcloud).',
    'nom_invalide': 'Nom refusé : caractères \\ / : * ? " < > | interdits, pas de point au début ni à la fin, 200 caractères au plus.',
    'cle_requise': 'Demande incomplète : rechargez la page.',
    'recherche_trop_courte': 'Tapez au moins deux caractères.',
    'recherche_trop_longue': 'Recherche trop longue.',
    'selection_vide': 'Sélectionnez au moins un élément.',
    'selection_hors_dossier': 'La sélection doit appartenir au dossier affiché.',
    'lot_trop_grand': 'Trop d’éléments à la fois (500 au plus).',
    'texte_a_chercher_requis': 'Indiquez le texte à remplacer.',
    'numerotation_invalide': 'Numérotation invalide (premier numéro de 0 à 99999, zéros de tête de 0 à 5).',
    'mode_renommage_invalide': 'Choisissez une façon de renommer.',
    'couleur_invalide': 'Couleur inconnue.', 'note_invalide': 'Note trop longue (2 000 caractères au plus) ou caractères invalides.',
    'favoris_trop_nombreux': 'Cinquante favoris au plus : retirez-en un d’abord.',
    'archive_zip_requise': 'Choisissez une archive .zip.',
    'archive_trop_de_fichiers': 'Trop de fichiers pour une archive (1 000 au plus).',
    'fichier_trop_volumineux': 'Fichier trop volumineux pour cette action.',
    'dossier_trop_profond': 'Arborescence trop profonde.',
    'chemin_est_un_dossier': 'Cette action porte sur un fichier, pas sur un dossier.',
    'non_pris_en_charge': 'Action non proposée par ce stockage.',
    'dossier_local_invalide': 'Dossier du poste refusé : indiquez le chemin complet d’un dossier existant (ni la racine d’un disque, ni le dossier d’AxiorHub).',
    'dossier_local_en_double': 'Ce dossier est indiqué deux fois.', 'reglages_invalides': 'Réglage hors des bornes proposées.',
    'requete_trop_longue': 'Demande trop longue.', 'json_requis': 'Format de demande refusé.', 'json_invalide': 'Format de demande refusé.',
    'objet_json_requis': 'Format de demande refusé.', 'action_invalide': 'Action inconnue.', 'etag_invalide': 'Demande invalide : rechargez le dossier.',
    'version_illisible': 'Cette version est illisible dans la réserve du poste.',
}
ARCHIVE = {'chemin_sortant': 'une entrée sortirait du dossier d’extraction (« .. » ou chemin absolu)', 'nom_interdit': 'nom de fichier interdit',
           'lien_symbolique': 'lien symbolique', 'archive_chiffree': 'archive chiffrée', 'doublon': 'deux entrées portent le même nom',
           'trop_d_entrees': 'plus de 1 000 entrées', 'trop_volumineuse_decompressee': 'plus de 200 Mo une fois décompressée',
           'taux_de_compression_suspect': 'taux de compression suspect (bombe de décompression)', 'archive_illisible': 'archive illisible',
           'archive_trop_volumineuse': 'archive de plus de 100 Mo', 'taille_annoncee_fausse': 'taille annoncée fausse'}


def allowed(role, method, api):
    """``standalone_auth.allowed`` : liste fermée de routes ; réglages du poste réservés à l'administrateur et à l'avocat (403 sinon)."""
    reste = str(api or '').split('?', 1)[0][len(PREFIXE):].strip('/')
    if method in ('GET', 'HEAD'):
        return reste in LECTURES
    if method != 'POST' or reste not in ECRITURES:
        return False
    return role in ROLES_POSTE if reste in POSTE_SEUL else role in ('administrateur', 'avocat', 'assistant')


def _prive(out):
    out['headers'] = [h for h in out.get('headers', []) if h[0].lower() != 'cache-control'] + [('Cache-Control', 'private, no-store')]
    return out


def _json(data, status='200 OK'):
    from .web440 import json_out
    return _prive(json_out(data, status))


def message(code):
    if code.startswith('archive_refusee:'):
        raison = code.split(':', 1)[1]
        return 'Archive refusée, rien n’a été extrait : %s.' % ARCHIVE.get(raison, raison.replace('_', ' '))
    return MESSAGES.get(code, 'Demande refusée.')


# ------------------------------------------------------------------------------------------------------------------- routes
def api(env, desk, auth, prefix, name, args, method):
    from .web567 import actor
    acteur = actor(env)
    reste = str(name).split('?', 1)[0][len(PREFIXE):].strip('/')
    if reste not in LECTURES + ECRITURES:
        return _json({'error': 'introuvable', 'message': MESSAGES['introuvable']}, STATUTS['introuvable'])
    if not allowed(acteur[1], method, name):
        code = 'role_insuffisant' if method == 'POST' and reste in POSTE_SEUL else 'introuvable'
        return _json({'error': code, 'message': MESSAGES[code]}, STATUTS[code])
    try:
        if method in ('GET', 'HEAD'):
            return _json(_lire(desk, auth, acteur, reste, args))
        if int(env.get('CONTENT_LENGTH') or 0) > MAX_POST:
            raise Stop('requete_trop_longue')
        from .web440 import check_post
        data = check_post(env, auth)
        return _json(_ecrire(desk, auth, acteur, reste, data))
    except Stop as ex:
        code = str(ex) if str(ex).startswith('archive_refusee:') else str(ex).split(':')[0]
        if code == 'dossier_reserve':
            code = 'introuvable'
        corps = {'error': code.split(':')[0], 'message': message(code)}
        detail = getattr(ex, 'detail', None)
        if code == 'conflits_renommage' and isinstance(detail, dict):
            corps['lignes'] = detail.get('lignes', [])
        return _json(corps, STATUTS.get(code.split(':')[0], '422 Unprocessable Entity'))


def _lire(desk, auth, acteur, reste, args):
    s, p = str(args.get('s') or ''), str(args.get('p') or '')
    if reste == 'lister':
        return F.lister(desk, auth, acteur, s, p)
    if reste == 'proprietes':
        return F.proprietes(desk, auth, acteur, s, p, empreinte=str(args.get('empreinte') or '') == '1')
    if reste == 'versions':
        return F.versions(desk, auth, acteur, s, p)
    return {'favoris': F.favoris(desk, auth, acteur)}


def _ecrire(desk, auth, acteur, reste, d):
    s, p, cle = str(d.get('s') or ''), str(d.get('p') or ''), d.get('cle')
    if reste == 'chercher':
        return F.chercher(desk, auth, acteur, s, p, d.get('q'), d.get('curseur'))
    if reste == 'apercu':
        return F.apercu_renommage(desk, auth, acteur, s, p, d.get('elements'), d.get('regle'))
    if reste == 'renommer':
        return F.renommer_lot(desk, auth, acteur, s, p, d.get('elements'), d.get('regle') or {}, cle)
    if reste == 'transferer':
        return F.transferer(desk, auth, acteur, str(d.get('mode') or ''), s, p, d.get('etag'), str(d.get('source_cible') or ''),
                            str(d.get('dossier_cible') or ''), cle)
    if reste == 'supprimer':
        return F.corbeille(desk, auth, acteur, s, p, d.get('etag'), cle)
    if reste == 'dossier':
        return F.creer_dossier(desk, auth, acteur, s, p, d.get('nom'), cle)
    if reste == 'note':
        return F.noter(desk, auth, acteur, s, p, d.get('couleur'), d.get('note'), d.get('version'), cle)
    if reste == 'favori':
        return F.favori(desk, auth, acteur, str(d.get('action') or ''), s, p, cle)
    if reste == 'zip':
        return F.creer_zip(desk, auth, acteur, s, p, d.get('chemins'), d.get('nom'), cle)
    if reste == 'extraire':
        return F.extraire_zip(desk, auth, acteur, s, p, cle)
    if reste == 'restaurer':
        return F.restaurer(desk, auth, acteur, s, p, d.get('version'), d.get('etag'), cle)
    if reste == 'verrou':
        return F.verrouiller(desk, auth, acteur, s, p, str(d.get('action') or ''), cle)
    if not F.poste(auth):
        raise Stop('introuvable')
    if reste == 'poste':
        return F.regler_local(desk, auth, acteur, d.get('dossiers'), d.get('reglages') or {}, cle)
    return F.executer(desk, acteur, cle, 'poste_scruter', {}, lambda: {'ok': True, 'bilan': F.scruter(desk),
                                                                          'message': 'Vérification du poste terminée.'})


# ------------------------------------------------------------------------------------------------------------------- page
def page_route(env, cfg, auth, prefix, path, args, shell):
    from .desk import Desk
    from .web440 import page_out
    from .web567 import actor
    from .shell501 import set_context
    set_context(cfg)
    acteur = actor(env)
    desk = Desk(cfg)
    try:
        corps = page_html(desk, auth, acteur, prefix)
    finally:
        desk.db.close()
    head = '<script defer src="%s/static/v56103-fichiers.js"></script>' % e(prefix, quote=True)
    return _prive(page_out(shell('Fichiers', corps, prefix, auth['csrf'], PAGE, head=head)))


def _bouton(action, libelle, aide='', classe='ax-btn ghost'):
    return '<button type="button" class="%s" data-f103="%s"%s>%s</button>' % (classe, action, (' title="%s"' % e(aide, quote=True)) if aide else '', e(libelle))


def _volet(n, options):
    actions = ''.join((
        _bouton('dossier', 'Nouveau dossier'),
        _bouton('renommer', 'Renommer par lot', 'Renommer plusieurs éléments avec aperçu, dont la numérotation des pièces'),
        _bouton('copier', 'Copier vers l’autre volet'),
        _bouton('deplacer', 'Déplacer vers l’autre volet'),
        _bouton('zip', 'Créer une archive ZIP'),
        _bouton('extraire', 'Extraire l’archive'),
        _bouton('proprietes', 'Propriétés'),
        _bouton('versions', 'Versions'),
        _bouton('note', 'Note et couleur'),
        _bouton('verrou', 'Verrouiller'),
        _bouton('supprimer', 'Mettre à la corbeille', 'Nextcloud seulement : restaurable depuis la corbeille de Nextcloud', 'ax-btn danger ghost'),
    ))
    return ('<section class="f103-volet" data-volet="%(n)d" id="f103-volet-%(n)d" aria-label="Volet %(h)d" role="tabpanel" aria-labelledby="f103-bascule-%(n)d">'
            '<div class="f103-onglets"><div class="f103-onglets-liste" role="tablist" aria-label="Onglets du volet %(h)d" data-f103-onglets></div>'
            '<button type="button" class="ax-btn ghost f103-plus" data-f103="onglet" aria-label="Nouvel onglet dans le volet %(h)d">+ Onglet</button></div>'
            '<div class="f103-barre"><label class="f103-champ">Source <select data-f103-source>%(o)s</select></label>'
            '<button type="button" class="ax-btn ghost" data-f103="favori" aria-pressed="false">☆ Favori</button>'
            '<label class="f103-champ f103-filtre">Filtrer ce dossier <input type="search" data-f103-filtre autocomplete="off" '
            'placeholder="Nom, extension…"></label></div>'
            '<form class="f103-chercher" data-f103-chercher><label class="f103-champ">Chercher dans les sous-dossiers '
            '<input type="search" name="q" minlength="2" maxlength="120" autocomplete="off"></label>'
            '<button type="submit" class="ax-btn ghost">Chercher</button>'
            '<button type="button" class="ax-btn ghost" data-f103="chercher-suite" hidden>Continuer la recherche</button>'
            '<button type="button" class="ax-btn ghost" data-f103="chercher-stop" hidden>Arrêter</button></form>'
            '<nav class="f103-fil" aria-label="Emplacement dans le volet %(h)d" data-f103-fil></nav>'
            '<div class="f103-actions" role="toolbar" aria-label="Actions sur la sélection du volet %(h)d">%(a)s</div>'
            '<p class="f103-etat" role="status" aria-live="polite" data-f103-etat>Chargement…</p>'
            '<div class="f103-liste" data-f103-zone><table><caption class="f103-masque">Contenu du dossier, volet %(h)d</caption><thead><tr>'
            '<th scope="col"><span class="f103-masque">Sélection</span></th><th scope="col">Nom</th><th scope="col">Taille</th>'
            '<th scope="col">Modifié</th><th scope="col">Note</th></tr></thead><tbody data-f103-lignes></tbody></table></div>'
            '</section>') % {'n': n, 'h': n + 1, 'o': options, 'a': actions}


def _couleurs_html():
    return ''.join('<label class="f103-couleur f103-c-%s"><input type="radio" name="couleur" value="%s"> %s</label>' % (k or 'aucune', k, e(v))
                   for k, v in F.COULEURS.items())


DIALOGUES = (
    '<dialog class="f103-dlg" id="f103-dlg-dossier" aria-labelledby="f103-t-dossier"><form method="dialog" data-f103-form="dossier">'
    '<h2 id="f103-t-dossier">Nouveau dossier</h2><label class="f103-champ">Nom <input name="nom" required maxlength="200"></label>'
    '<p class="f103-statut" role="status" aria-live="polite"></p><div class="f103-pied"><button type="submit" class="ax-btn">Créer le dossier</button>'
    '<button type="button" class="ax-btn ghost" data-f103-fermer>Annuler</button></div></form></dialog>'

    '<dialog class="f103-dlg f103-large" id="f103-dlg-renommer" aria-labelledby="f103-t-renommer"><form method="dialog" data-f103-form="renommer">'
    '<h2 id="f103-t-renommer">Renommer par lot</h2>'
    '<fieldset><legend>Façon de renommer</legend>'
    '<label><input type="radio" name="mode" value="pieces" checked> Numéroter les pièces (« Pièce n° 1 – … »)</label>'
    '<label><input type="radio" name="mode" value="remplacer"> Remplacer un texte</label>'
    '<label><input type="radio" name="mode" value="ajouter"> Ajouter au début ou à la fin</label></fieldset>'
    '<div class="f103-regle" data-mode="pieces"><label class="f103-champ">Premier numéro <input type="number" name="debut" value="1" min="0" max="99999"></label>'
    '<label class="f103-champ">Zéros de tête <select name="zeros"><option value="0">Aucun (1, 2… 10)</option><option value="2">01, 02…</option>'
    '<option value="3">001, 002…</option></select></label>'
    '<label><input type="checkbox" name="garder_nom" checked> Garder le nom actuel après le numéro</label></div>'
    '<div class="f103-regle" data-mode="remplacer" hidden><label class="f103-champ">Texte à remplacer <input name="cherche" maxlength="200"></label>'
    '<label class="f103-champ">Remplacer par <input name="remplace" maxlength="200"></label></div>'
    '<div class="f103-regle" data-mode="ajouter" hidden><label class="f103-champ">Au début <input name="prefixe" maxlength="200"></label>'
    '<label class="f103-champ">À la fin (avant l’extension) <input name="suffixe" maxlength="200"></label></div>'
    '<h3>Ordre et aperçu</h3><p class="f103-aide">L’ordre de la liste donne les numéros. Déplacez une ligne avec ses boutons ↑ ↓. '
    'L’extension ne change jamais.</p><div class="f103-tableau"><table><thead><tr><th scope="col">Ordre</th><th scope="col">Avant</th>'
    '<th scope="col">Après</th><th scope="col">Contrôle</th></tr></thead><tbody data-f103-apercu></tbody></table></div>'
    '<p class="f103-statut" role="status" aria-live="polite"></p><div class="f103-pied"><button type="button" class="ax-btn ghost" data-f103-voir>Voir l’aperçu</button>'
    '<button type="submit" class="ax-btn" disabled>Renommer</button><button type="button" class="ax-btn ghost" data-f103-fermer>Fermer</button></div></form></dialog>'

    '<dialog class="f103-dlg" id="f103-dlg-proprietes" aria-labelledby="f103-t-proprietes"><form method="dialog">'
    '<h2 id="f103-t-proprietes">Propriétés</h2><dl class="f103-props" data-f103-props></dl>'
    '<p class="f103-statut" role="status" aria-live="polite"></p><div class="f103-pied">'
    '<button type="button" class="ax-btn ghost" data-f103-empreinte>Calculer l’empreinte SHA-256</button>'
    '<button type="button" class="ax-btn ghost" data-f103-fermer>Fermer</button></div></form></dialog>'

    '<dialog class="f103-dlg f103-large" id="f103-dlg-versions" aria-labelledby="f103-t-versions"><form method="dialog">'
    '<h2 id="f103-t-versions">Versions</h2><p class="f103-aide">Restaurer ne remplace jamais une version plus récente : si le fichier a changé '
    'depuis l’affichage, la version choisie est enregistrée dans une copie de conflit, signalée.</p>'
    '<div class="f103-tableau"><table><thead><tr><th scope="col">Date et heure</th><th scope="col">Auteur</th><th scope="col">Taille</th>'
    '<th scope="col">Action</th></tr></thead><tbody data-f103-versions></tbody></table></div>'
    '<p class="f103-statut" role="status" aria-live="polite"></p><div class="f103-pied"><button type="button" class="ax-btn ghost" data-f103-fermer>Fermer</button></div>'
    '</form></dialog>'

    '<dialog class="f103-dlg" id="f103-dlg-note" aria-labelledby="f103-t-note"><form method="dialog" data-f103-form="note">'
    '<h2 id="f103-t-note">Note et couleur</h2><p class="f103-aide">Rangées dans AxiorHub (jamais dans le fichier) et visibles par le cabinet.</p>'
    '<fieldset class="f103-couleurs"><legend>Couleur</legend>%s</fieldset>'
    '<label class="f103-champ">Note <textarea name="note" rows="4" maxlength="2000"></textarea></label>'
    '<p class="f103-statut" role="status" aria-live="polite"></p><div class="f103-pied"><button type="submit" class="ax-btn">Enregistrer la note</button>'
    '<button type="button" class="ax-btn ghost" data-f103-fermer>Annuler</button></div></form></dialog>'

    '<dialog class="f103-dlg" id="f103-dlg-transfert" aria-labelledby="f103-t-transfert"><form method="dialog" data-f103-form="transfert">'
    '<h2 id="f103-t-transfert">Copier ou déplacer</h2><p data-f103-question></p>'
    '<p class="f103-statut" role="status" aria-live="polite"></p><div class="f103-pied">'
    '<button type="button" class="ax-btn" data-f103-choix="copier">Copier</button>'
    '<button type="button" class="ax-btn ghost" data-f103-choix="deplacer">Déplacer</button>'
    '<button type="button" class="ax-btn ghost" data-f103-fermer>Annuler</button></div></form></dialog>'

    '<dialog class="f103-dlg" id="f103-dlg-zip" aria-labelledby="f103-t-zip"><form method="dialog" data-f103-form="zip">'
    '<h2 id="f103-t-zip">Créer une archive ZIP</h2><label class="f103-champ">Nom de l’archive <input name="nom" maxlength="200" '
    'placeholder="Archive (date et heure)"></label><p class="f103-statut" role="status" aria-live="polite"></p><div class="f103-pied">'
    '<button type="submit" class="ax-btn">Créer l’archive</button><button type="button" class="ax-btn ghost" data-f103-fermer>Annuler</button></div></form></dialog>'

    '<dialog class="f103-dlg" id="f103-dlg-confirmer" aria-labelledby="f103-t-confirmer"><form method="dialog" data-f103-form="confirmer">'
    '<h2 id="f103-t-confirmer">Confirmer</h2><p data-f103-question></p><p class="f103-statut" role="status" aria-live="polite"></p>'
    '<div class="f103-pied"><button type="submit" class="ax-btn">Confirmer</button><button type="button" class="ax-btn ghost" data-f103-fermer>Annuler</button></div>'
    '</form></dialog>'
) % _couleurs_html()


def _poste_html(desk):
    """Réglages du disque local (mode poste) : dossiers ouverts, surveillance, plafond et durée ; état du dernier passage (lecture seule)."""
    dossiers = F.dossiers_locaux(desk)
    r = F.reglages_locaux(desk)
    etat = F.etat_local(desk)
    lignes = ''.join(
        '<li class="f103-poste-ligne"><label class="f103-champ">Chemin du dossier <input name="chemin" value="%s" maxlength="400"></label>'
        '<label class="f103-champ">Nom affiché <input name="nom" value="%s" maxlength="80"></label>'
        '<label><input type="checkbox" name="surveille"%s> Garder une version à chaque changement</label>'
        '<button type="button" class="ax-btn ghost" data-f103-retirer>Retirer</button></li>' % (
            e(d['chemin'], quote=True), e(d.get('nom') or '', quote=True), ' checked' if d.get('surveille') else '') for d in dossiers)
    dernier = etat.get('le') or ''
    return ('<details class="f103-poste" id="f103-poste"><summary>Dossiers de ce poste et versions locales</summary>'
            '<form data-f103-form="poste"><p class="f103-aide">Seuls les dossiers indiqués ici sont ouverts dans la page (mode poste). Pour un dossier '
            'surveillé, AxiorHub garde une copie à chaque changement, même fait dans Word, dans son dossier de travail ; le plafond et la durée '
            'ci-dessous limitent l’espace occupé (la copie la plus récente de chaque fichier est toujours gardée).</p>'
            '<ul class="f103-poste-liste" data-f103-poste-liste>%s</ul><button type="button" class="ax-btn ghost" data-f103-ajouter>Ajouter un dossier</button>'
            '<div class="f103-poste-reglages"><label class="f103-champ">Plafond d’espace (Mo) <input type="number" name="plafond_mo" min="%d" max="%d" value="%d"></label>'
            '<label class="f103-champ">Durée de conservation (jours) <input type="number" name="jours" min="%d" max="%d" value="%d"></label>'
            '<label class="f103-champ">Vérifier toutes les (secondes) <input type="number" name="intervalle" min="%d" max="%d" value="%d"></label></div>'
            '<p class="f103-aide">Dernière vérification : %s · %d copie%s gardée%s, %s Mo occupés.</p>'
            '<p class="f103-statut" role="status" aria-live="polite"></p><div class="f103-pied"><button type="submit" class="ax-btn">Enregistrer les réglages du poste</button>'
            '<button type="button" class="ax-btn ghost" data-f103-scruter>Vérifier maintenant</button></div></form></details>') % (
        lignes, *F.LOCAL_BORNES['plafond_mo'], r['plafond_mo'], *F.LOCAL_BORNES['jours'], r['jours'], *F.LOCAL_BORNES['intervalle'], r['intervalle'],
        e(dernier.replace('T', ' ')[:16] if dernier else 'jamais'), etat.get('copies', 0), 's' if etat.get('copies', 0) > 1 else '',
        's' if etat.get('copies', 0) > 1 else '', e(str(round((etat.get('octets') or 0) / 1_000_000, 1)).replace('.', ',')))


def page_html(desk, auth, acteur, prefix):
    from .interface56100 import active
    disponibles = F.sources(desk, auth, acteur)
    simple = not active(desk, acteur[0])
    entete = ('<h1 id="f103-titre">Fichiers</h1><p class="f103-note">Fichiers du cabinet dans Nextcloud%s. Rien n’est lu par l’IA depuis cette page. '
              'Supprimer place l’élément dans la corbeille de Nextcloud ; un nom déjà pris n’est jamais écrasé.</p>') % (
        ' et dossiers choisis de ce poste' if 'local' in disponibles else '')
    if not disponibles:   # la page garde sa structure (aide, raccourcis) ; aucun volet n'est chargé tant qu'aucun stockage n'est configuré
        entete += ('<p class="f103-alerte" role="status">Aucun stockage de fichiers n’est configuré : renseignez Nextcloud dans '
                   '<a href="%s/guide-installation?etape=stockage">le guide d’installation</a>.</p>') % e(prefix, quote=True)
    try:
        favoris = F.favoris(desk, auth, acteur, disponibles)
    except Stop:
        favoris = []
    options = ''.join('<option value="%s">%s</option>' % (e(i, quote=True), e(s.libelle)) for i, s in disponibles.items())
    config = {'api': prefix + '/api440/' + PREFIXE, 'sources': [{'id': i, 'libelle': s.libelle} for i, s in disponibles.items()],
              'favoris': favoris, 'couleurs': F.COULEURS, 'simple': simple}
    volets = _volet(0, options) + ('' if simple else _volet(1, options))
    bascule = '' if simple else (
        '<div class="f103-bascule" role="tablist" aria-label="Volets">'
        '<button type="button" role="tab" id="f103-bascule-0" aria-controls="f103-volet-0" aria-selected="true" data-f103-bascule="0">Volet 1</button>'
        '<button type="button" role="tab" id="f103-bascule-1" aria-controls="f103-volet-1" aria-selected="false" data-f103-bascule="1" tabindex="-1">Volet 2</button></div>')
    favoris_html = ('<section class="f103-favoris" aria-labelledby="f103-t-favoris"><h2 id="f103-t-favoris">Favoris</h2><ul data-f103-favoris>%s</ul></section>' % (
        ''.join('<li><button type="button" class="ax-btn ghost" data-f103-aller="%s" data-source="%s">%s</button></li>' % (
            e(f['chemin'], quote=True), e(f['source'], quote=True), e(f['nom'] or f['chemin'])) for f in favoris) or
        '<li class="f103-vide">Aucun favori : ouvrez un dossier puis « ☆ Favori ».</li>'))
    return ('<section class="f103%s" id="f103" aria-labelledby="f103-titre" data-f103-config="%s">%s%s<div class="f103-volets">%s</div>%s%s'
            '<p class="f103-aide f103-clavier">Clavier : Entrée ouvre, Espace sélectionne, Ctrl+A sélectionne tout, F2 renomme, Suppr met à la corbeille. '
            'Glisser un élément vers l’autre volet propose de le copier ou de le déplacer (déplacement confirmé).</p>%s</section>') % (
        ' f103-simple' if simple else '', e(json.dumps(config, ensure_ascii=False), quote=True), entete, bascule, volets, favoris_html,
        _poste_html(desk) if 'local' in disponibles else '', DIALOGUES)
