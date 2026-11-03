"""5.6.89 : composants communs de l'interface (audit 5.6.88, § 17). Du HTML côté serveur, habillé par ``v5689-carbon.css``.

Principe des champs : le serveur rend toujours un contrôle natif, complet et utilisable ; ``v5689-formulaires.js`` lui adjoint le composant Carbon quand celui-ci est chargé, et garde le natif
synchronisé. Un composant qui ne charge pas laisse donc un formulaire qui fonctionne, et les scripts existants lisent toujours les mêmes ``input``.

États : chargement, vide, partiel, erreur et non configuré sont cinq états distincts, chacun avec son texte. Une couleur ne porte jamais seule un sens : icône et libellé l'accompagnent.
"""
from html import escape as e
import json
import uuid

from .ui_icones5689 import ICONES

ETIQUETTES = {'info': 'blue', 'succes': 'green', 'attention': 'warm-gray', 'erreur': 'red', 'neutre': 'cool-gray', 'ia': 'purple'}
ETATS = {'chargement': ('actualiser', 'status'), 'vide': ('info', 'status'), 'partiel': ('vigilance', 'status'), 'erreur': ('erreur', 'alert'), 'non_configure': ('reglages', 'status')}


def icone(nom, titre='', taille=20, classe=''):
    """SVG local. Sans titre, elle est décorative et ignorée du lecteur d'écran ; avec un titre, elle porte ce nom accessible."""
    attr = ('role="img" aria-label="%s"' % e(titre, quote=True)) if titre else 'aria-hidden="true"'
    return '<svg class="ui89-icone%s" width="%d" height="%d" viewBox="0 0 32 32" fill="currentColor" focusable="false" %s>%s</svg>' % (' ' + e(classe, quote=True) if classe else '', taille, taille, attr, ICONES[nom])


def _ident(prefixe):
    return prefixe + '-' + uuid.uuid4().hex[:8]


def etiquette(texte, genre='neutre', titre=''):
    """Étiquette d'état : le texte reste lisible si le composant n'est pas chargé (règle ``cds-tag:not(:defined)``)."""
    title = (' title="%s"' % e(titre, quote=True)) if titre else ''
    return '<cds-tag size="md" type="%s" class="ui89-tag" data-genre="%s"%s>%s</cds-tag>' % (ETIQUETTES.get(genre, 'cool-gray'), e(genre, quote=True), title, e(texte))


def etat_html(genre, titre, detail='', action=''):
    """Bloc d'état d'une zone : ``genre`` dans ETATS. L'erreur est une alerte ; les autres se lisent sans interrompre."""
    nom, role = ETATS[genre]
    return '<div class="ui89-etat" data-etat="%s" role="%s">%s<div><strong>%s</strong>%s%s</div></div>' % (
        genre, role, icone(nom), e(titre), ('<p>%s</p>' % e(detail)) if detail else '', action)


def formulaire(api, contenu, recharge=False, classe='', attributs=''):
    """Formulaire à traitement commun : ``data-carbon`` demande l'adjonction des composants Carbon à ses champs natifs."""
    return '<form class="m5-form ui89-form%s" data-api="%s" data-carbon%s%s>%s<p class="m5-status" role="status"></p></form>' % (
        ' ' + e(classe, quote=True) if classe else '', e(api, quote=True), ' data-reload="1"' if recharge else '', (' ' + attributs) if attributs else '', contenu)


def _aide(texte, ident):
    return '<span class="ui89-aide" id="%s">%s</span>' % (ident, e(texte)) if texte else ''


def case(nom, libelle, coche=False, aide='', desactive=False):
    ident = _ident('c')
    return '<div class="ui89-champ" data-champ="case"><label class="m5-check"><input type="checkbox" name="%s"%s%s%s> <span>%s</span></label>%s</div>' % (
        e(nom, quote=True), ' checked' if coche else '', ' disabled' if desactive else '', (' aria-describedby="%s"' % ident) if aide else '', e(libelle), _aide(aide, ident))


def radios(nom, legende, options, valeur='', requis=False):
    lignes = ''.join('<label class="m5-check"><input type="radio" name="%s" value="%s"%s%s> <span>%s</span></label>' % (
        e(nom, quote=True), e(v, quote=True), ' checked' if v == valeur else '', ' required' if requis else '', e(t)) for v, t in options)
    return '<fieldset class="ui89-champ" data-champ="radios"><legend>%s</legend>%s</fieldset>' % (e(legende), lignes)


def liste(nom, libelle, options, valeur='', requis=False, aide=''):
    ident = _ident('l')
    opts = ''.join('<option value="%s"%s>%s</option>' % (e(v, quote=True), ' selected' if v == valeur else '', e(t)) for v, t in options)
    return '<label class="m5-field ui89-champ" data-champ="liste"><span>%s</span><select name="%s"%s%s>%s</select>%s</label>' % (
        e(libelle), e(nom, quote=True), ' required' if requis else '', (' aria-describedby="%s"' % ident) if aide else '', opts, _aide(aide, ident))


def texte(nom, libelle, valeur='', requis=False, aide='', genre='text', max_car=0):
    ident = _ident('t')
    return '<label class="m5-field ui89-champ" data-champ="texte"><span>%s</span><input type="%s" name="%s" value="%s"%s%s%s>%s</label>' % (
        e(libelle), e(genre, quote=True), e(nom, quote=True), e(valeur, quote=True), ' required' if requis else '', (' maxlength="%d"' % max_car) if max_car else '',
        (' aria-describedby="%s"' % ident) if aide else '', _aide(aide, ident))


def zone_texte(nom, libelle, valeur='', requis=False, aide='', lignes=4):
    ident = _ident('z')
    return '<label class="m5-field ui89-champ" data-champ="zone"><span>%s</span><textarea name="%s" rows="%d"%s%s>%s</textarea>%s</label>' % (
        e(libelle), e(nom, quote=True), lignes, ' required' if requis else '', (' aria-describedby="%s"' % ident) if aide else '', e(valeur), _aide(aide, ident))


def filtres(etiquette_aria, entrees, actif, panneau):
    """Onglets de filtre : tous restent visibles, même à zéro (§ 17), pour distinguer « rien » d'« absent ». ``entrees`` : (clé, libellé, compteur ou None si inconnu)."""
    boutons = []
    for cle, libelle, nombre in entrees:
        marque = '%d' % nombre if nombre is not None else 'à vérifier'
        boutons.append('<button type="button" class="ui89-onglet" role="tab" id="%s-onglet-%s" data-filtre="%s" aria-selected="%s" aria-controls="%s" tabindex="%d">%s <span class="ui89-compte">%s</span></button>' % (
            panneau, e(cle, quote=True), e(cle, quote=True), 'true' if cle == actif else 'false', panneau, 0 if cle == actif else -1, e(libelle), e(marque)))
    return '<div class="ui89-onglets" role="tablist" aria-label="%s" data-onglets="%s">%s</div>' % (e(etiquette_aria, quote=True), panneau, ''.join(boutons))


def action_ia_html(fonction='', contexte=None, allume=None):
    """Même icône IA partout : elle ouvre l'assistant existant sur cette page et cet objet (``v5689-ia.js``). Drapeau ``actions_ia`` éteint : rien n'est affiché et aucune action n'est suggérée.

    ``contexte`` décrit ce qui est affiché : ``dossier``, ``objets`` (``id``, ``version_id``), ``titre``, ``page`` (vrai : objet principal de la page). Ce n'est qu'une description : le serveur revalide chaque identifiant à l'ouverture."""
    if allume is None:
        from .shell501 import drapeaux
        allume = drapeaux().get('actions_ia')
    if not allume:
        return '<span class="ui89-ia-slot" data-fonction="%s" hidden></span>' % e(fonction, quote=True)
    contexte = contexte or {}
    objets = [{'id': str(o.get('id') or ''), 'version_id': str(o.get('version_id') or '')} for o in contexte.get('objets') or () if o.get('id')]
    titre = 'Demander à l’assistant' + (' : ' + str(contexte['titre']) if contexte.get('titre') else '')
    return '<button type="button" class="ui89-ia" data-ia-fonction="%s" data-ia-dossier="%s" data-ia-objets="%s"%s title="%s" aria-label="%s">%s</button>' % (
        e(fonction, quote=True), e(str(contexte.get('dossier') or ''), quote=True), e(json.dumps(objets, ensure_ascii=False), quote=True), ' data-ia-page="1"' if contexte.get('page') else '', e(titre, quote=True), e(titre, quote=True), icone('ia'))
