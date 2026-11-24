"""5.6.98 : onglets Parties, Juridiction et Fichiers de la fiche du dossier (``dossier5679``).

HTML rendu par le serveur et échappé (``html.escape``). Les formulaires sont des ``form.d97-form[data-d97-api]`` : le script commun
``v5697-dossiers.js`` (chargé sur toutes les pages) envoie le JSON avec le jeton CSRF et la clé d'idempotence rendue ici, puis affiche les
erreurs champ par champ ; ``v5698-fiche.js`` ajoute la navigation au clavier des sous-onglets et la confirmation d'un retrait. Aucun script
en ligne, aucun HTML venant du serveur injecté par script, aucun appel à un modèle. Dossier réservé : la page entière répond 404 avant
d'arriver ici ; chaque lecture repasse en outre par la garde du registre (``fiche5698._dossier``).
"""
from html import escape as e
from urllib.parse import quote, urlencode
import uuid

from .common import Stop
from . import dossiers5697 as D
from . import fiche5698 as F


def _cle():
    return uuid.uuid4().hex


def _q(prefix, mid, view, **extra):
    return e(prefix + '/dossier?' + urlencode({'id': mid, 'vue': view, **{k: v for k, v in extra.items() if v not in (None, '')}}), quote=True)


def _api(*morceaux):
    return e('m57/dossiers/' + '/'.join(quote(str(m), safe='') for m in morceaux), quote=True)


def _date_fr(valeur):
    from .dossiers_ui5697 import _date_fr as fr
    return fr(valeur)


def _options(choix, actuel, exclure=()):
    return ''.join('<option value="%s"%s>%s</option>' % (e(k, quote=True), ' selected' if k == actuel else '', e(v))
                   for k, v in choix.items() if k not in exclure or k == actuel)


def options_roles(actuel='', groupes=None, exclure=F.ROLES_HORS_FORMULAIRE):
    """Rôles regroupés comme dans l'onglet Parties (``<optgroup>``) : formulaire Nouveau dossier et formulaires de l'onglet."""
    out = ['<option value="" selected>Choisir le rôle</option>'] if not actuel else []
    for cle, libelle, roles in F.GROUPES:
        if groupes and cle not in groupes:
            continue
        opts = ''.join('<option value="%s"%s>%s</option>' % (r, ' selected' if r == actuel else '', e(D.ROLES_PARTIES[r]))
                       for r in roles if r not in exclure or r == actuel)
        if opts:
            out.append('<optgroup label="%s">%s</optgroup>' % (e(libelle, quote=True), opts))
    return ''.join(out)


def _erreur(champ):
    return '<span class="d97-erreur" data-erreur="%s" role="alert"></span>' % e(champ, quote=True)


def _champ(libelle, champ, valeur='', maxi=160, type_='text', large=False, aide=''):
    return '<label class="d98-champ%s">%s<input name="%s" type="%s" value="%s" maxlength="%d" autocomplete="off"%s>%s%s</label>' % (
        ' d98-large' if large else '', e(libelle), champ, type_, e(valeur or '', quote=True), maxi, ' data-no-dictation' if type_ in ('email', 'tel') else '',
        ('<span class="d97-aide">%s</span>' % e(aide)) if aide else '', _erreur(champ))


def _formulaire(api, corps, bouton, cls='', confirmer=''):
    return ('<form class="d97-form d98-form%s" data-d97-api="%s" data-d97-apres="recharger" novalidate%s><input type="hidden" name="cle" value="%s">%s'
            '<div class="d98-actions"><button class="ax-btn%s" type="submit">%s</button><span class="d97-statut" role="status" aria-live="polite"></span></div></form>') % (
        (' ' + cls) if cls else '', api, (' data-d98-confirmer="%s"' % e(confirmer, quote=True)) if confirmer else '', _cle(), corps,
        ' ghost' if confirmer else '', e(bouton))


def _hors_registre(prefix, acteur):
    lien = (' <a href="%s">Préparer l’import (simulation d’abord)</a>' % e(prefix + '/dossiers/import', quote=True)) if acteur[1] == 'administrateur' else ''
    return ('<p class="d97-avis" role="note">Ce dossier n’est pas encore inscrit au registre unique des dossiers : il est affiché en lecture. '
            'L’administrateur du cabinet peut l’importer pour le modifier ici.%s</p>' % lien)


# ------------------------------------------------------------------------------------------------ Parties
def _corps_partie(p, avocats, groupe=None):
    p = p or {}
    premier_role = {g: roles[0] for g, _, roles in F.GROUPES}
    # 5.6.109 (C11) : sans rôle connu ni groupe choisi, le rôle reste à choisir (« Choisir le rôle ») : jamais « adverse » par défaut
    roles = options_roles(p.get('role') or premier_role.get(groupe, ''), (groupe,) if groupe else None)
    choix_avocat = '<option value="">Aucun / non désigné</option>' + ''.join(
        '<option value="%s"%s>%s (%s)</option>' % (e(a['id'], quote=True), ' selected' if a['id'] == p.get('avocat_id') else '', e(a['nom']), e(a['role_libelle']))
        for a in avocats if a['id'] != p.get('id'))
    return ('<div class="d98-grille">'
            + _champ('Nom ou dénomination', 'nom', p.get('nom'), 160)
            + '<label class="d98-champ">Rôle<select name="role">%s</select>%s</label>' % (roles, _erreur('role'))
            + _champ('Qualité', 'qualite', p.get('qualite'), 160, aide='Dans l’acte ou la procédure : demandeur, intimée, vendeur, bailleur, gérant…').replace(
                'name="qualite"', 'name="qualite" list="d98-qualites"', 1)
            + '<label class="d98-champ">Personne<select name="type_personne">%s</select>%s</label>' % (_options(F.TYPES_PERSONNE, p.get('type_personne', '')), _erreur('type_personne'))
            + _champ('Courriel', 'courriel', p.get('courriel'), 254, 'email')
            + _champ('Téléphone', 'telephone', p.get('telephone'), 30, 'tel')
            + _champ('Adresse', 'adresse', p.get('adresse'), 300, large=True)
            + _champ('SIREN (société)', 'siren', p.get('siren'), 11, aide='Facultatif, personne morale : 9 chiffres')
            + ('<label class="d98-champ">Représenté(e) par<select name="avocat_id">%s</select>%s</label>' % (choix_avocat, _erreur('avocat_id')))
            + '<label class="d98-champ d98-large">Note<textarea name="note" rows="2" maxlength="1000">%s</textarea>%s</label>' % (e(p.get('note') or ''), _erreur('note'))
            + '</div>')


def _provenance_html(prefix, did, p, plus, instances):
    """5.6.109 (C11, F04) : provenance de la partie (qui a choisi son rôle, pièce d'où elle vient) et qualités par instance — celles fixées
    par l'avocat priment, celles lues dans les pièces sont dites telles."""
    plus = plus or {}
    out = ''
    if p.get('provenance') == 'piece' or plus.get('sources'):
        qui = (' · rôle choisi par %s%s' % (e(p.get('role_par') or ''), (' le ' + e(_date_fr(p.get('role_le')))) if p.get('role_le') else '')) if p.get('role_par') else ''
        out += '<p class="d109-provenance ax-muted">%s%s %s</p>' % (e(F.PROVENANCES_PARTIE.get(p.get('provenance') or 'manuel', '')), qui,
                                                                  _sources_html(prefix, did, plus.get('sources')))
    roles = plus.get('roles_par_instance') or []
    if roles:
        out += '<ul class="d109-instances">%s</ul>' % ''.join('<li><strong>%s</strong> — %s%s</li>' % (
            e(r['qualite']), e(r['cadre'] or 'cadre à préciser'), '' if r['origine'] == 'avocat' else ' <span class="d109-statut">lu dans une pièce, à vérifier</span>') for r in roles)
    if instances and p['role'] not in F.ROLES_AVOCATS:
        fixes = {r['instance']: r for r in roles if r['origine'] == 'avocat'}
        lignes = ''.join(_formulaire(_api109(did, 'participations'), '<input type="hidden" name="partie" value="%s"><input type="hidden" name="instance" value="%s">'
                                     '<input type="hidden" name="version" value="%d"><label class="d98-champ d98-inline">%s<input name="qualite" value="%s" maxlength="160" '
                                     'list="d98-qualites" autocomplete="off"></label>%s' % (
                                         e(p['id'], quote=True), e(i['id'], quote=True), int((fixes.get(i['id']) or {}).get('version') or 0),
                                         e('%s — %s' % (i['degre_libelle'], i['juridiction'] or 'juridiction à préciser')),
                                         e((fixes.get(i['id']) or {}).get('qualite') or '', quote=True), _erreur('qualite')), 'Enregistrer', 'd98-form-ligne')
                         for i in instances[:6])
        out += '<details class="d98-editer d109-qualites"><summary>Qualité par instance</summary><p class="ax-muted">Une même personne peut être ' \
               'défenderesse en première instance puis appelante. Laissez vide pour retirer.</p>%s</details>' % lignes
    return out


def _api109(did, *morceaux):
    return e('m56109/fiche/dossiers/' + '/'.join(quote(str(m), safe='') for m in (did,) + morceaux), quote=True)


def _sources_html(prefix, did, sources, limite=2):
    """Liens vers les sources d'un élément (pièce, page, extrait en infobulle) ; l'accès est relu par le serveur à l'ouverture."""
    if not sources:
        return ''
    try:
        from .vue5651 import _source_links
        return _source_links(prefix, did, sources, limite)
    except Exception:   # noqa: BLE001 — lien indisponible : le libellé de la source reste affiché
        return '<span class="d51-sources">%s</span>' % ' · '.join(e(str(s.get('document') or s.get('label') or s.get('path') or '')) for s in sources[:limite])


def _ligne_partie(prefix, did, p, avocats, plus=None, instances=()):
    details = []
    if p['qualite']:
        details.append(e(p['qualite']))
    if p['type_libelle'] and p['type_personne']:
        details.append(e(p['type_libelle']))
    if p['siren']:
        details.append('SIREN ' + e(p['siren']))
    contact = ' · '.join(e(x) for x in (p['courriel'], p['telephone']) if x)
    adresse = ('<span class="d98-adresse">%s</span>' % e(p['adresse'])) if p['adresse'] else ''
    avocat = ('<p class="d98-avocat">Représenté(e) par <strong>%s</strong></p>' % e(p['avocat'])) if p['avocat'] else ''
    conflit = ''
    if p['conflit_statut']:
        conflit = '<p class="d98-conflit d98-conflit-%s" role="note"><strong>Conflits d’intérêts : %s.</strong> %s <span class="ax-muted">(%s)</span></p>' % (
            e(p['conflit_statut'], quote=True), e(p['conflit_libelle'] or p['conflit_statut']), e(p['conflit_resume']), e(_date_fr(p['conflit_le'])))
    note = ('<p class="d98-note">%s</p>' % e(p['note'])) if p['note'] else ''
    modifier = '<details class="d98-editer"><summary>Modifier</summary>%s</details>' % _formulaire(
        _api(did, 'parties', p['id']), '<input type="hidden" name="version" value="%d">' % p['version'] + _corps_partie(p, avocats), 'Enregistrer')
    retirer = _formulaire(_api(did, 'parties', p['id'], 'retirer'), '<input type="hidden" name="version" value="%d">' % p['version'], 'Retirer',
                          'd98-retirer', 'Retirer %s du dossier ?' % p['nom'])
    return ('<li class="d98-partie"><div class="d98-partie-tete"><strong class="d98-nom">%s</strong> <span class="d98-role">%s</span></div>'
            '<p class="d98-details">%s</p>%s%s%s%s%s%s<div class="d98-gestes">%s%s</div></li>') % (
        e(p['nom']), e(p['role_libelle']), ' · '.join(details) or '<span class="ax-muted">Qualité à préciser</span>',
        ('<p class="d98-contact">%s</p>' % contact) if contact else '', adresse, avocat, conflit, note, _provenance_html(prefix, did, p, plus, instances),
        modifier, retirer)


AJOUTER = {'clients': 'Ajouter un client', 'adverses': 'Ajouter une partie adverse', 'avocats': 'Ajouter un avocat', 'autres': 'Ajouter un intervenant',
           'contractants': 'Ajouter un cocontractant ou un tiers'}
VIDES = {'clients': 'Aucun client inscrit.', 'adverses': 'Aucune partie adverse inscrite.', 'avocats': 'Aucun avocat inscrit (du client, adverse ou postulant).',
         'autres': 'Aucun autre intervenant (expert, commissaire de justice, mandataire, notaire, assureur…).',
         'contractants': 'Aucun cocontractant ni tiers inscrit (vendeur, bailleur, cessionnaire… : une contrepartie n’est pas une partie adverse).'}


def parties_html(desk, acteur, prefix, m):
    try:
        data = F.parties(desk, acteur, m['id'])
    except Stop as ex:
        if str(ex) != 'introuvable':
            raise
        return _hors_registre(prefix, acteur) + _proposees_lecture(desk, m)
    did = data['dossier']
    avocats = [p for p in data['parties'] if p['role'] in F.ROLES_AVOCATS]
    try:   # 5.6.109 (F04) : provenance, sources et qualités par instance, lues par la projection commune (aucune écriture)
        from .projection56109 import rapprocher
        plus = {p['id']: p for p in rapprocher(desk, did)[0]}
        instances = F.instances(desk, acteur, did)['instances']
    except Exception:   # noqa: BLE001 — compléments indisponibles : les parties inscrites restent affichées et modifiables
        plus, instances = {}, ()
    out = ['<div class="d98" id="d98-parties"><p class="d98-intro ax-muted">Les parties inscrites ici sont celles du dossier ; elles servent aux courriers, '
           'aux conflits d’intérêts et à la recherche. Ajouter une partie adverse relance la vérification des conflits.</p>'
           '<datalist id="d98-qualites">%s</datalist>' % ''.join('<option value="%s">' % e(q, quote=True) for q in F.QUALITES)]
    for cle, libelle, liste in data['groupes']:
        lignes = ''.join(_ligne_partie(prefix, did, p, avocats, plus.get(p['id']), instances) for p in liste) or '<li class="ax-muted d98-vide">%s</li>' % e(VIDES[cle])
        ajout = '<details class="d98-ajout"><summary class="ax-btn ghost">%s</summary>%s</details>' % (
            e(AJOUTER[cle]), _formulaire(_api(did, 'parties'), _corps_partie(None, avocats, cle), 'Ajouter'))
        out.append('<section class="d98-groupe d79-card" aria-labelledby="d98-g-%s"><div class="d79-card-head"><h2 id="d98-g-%s">%s <span class="d98-compte">%d</span></h2></div>'
                   '<ul class="d98-liste">%s</ul>%s</section>' % (cle, cle, e(libelle), len(liste), lignes, ajout))
    try:
        proposees = F.parties_proposees(desk, did, data['parties'])
    except Exception:   # noqa: BLE001 — lecture des pièces indisponible : les parties inscrites restent affichées
        proposees = None
    out.append(_proposees_html(did, proposees, prefix, data['parties']))
    out.append('</div>')
    return ''.join(out)


def _proposee_ligne(did, p, prefix, inscrites):
    """Une mention lue dans les pièces : Accepter (rôle à choisir), Corriger, Fusionner, Écarter sur la même ligne, avec l'accès à l'extrait."""
    info = ' · '.join(e(x) for x in (p['qualite'], ('conseil : ' + p['conseil']) if p['conseil'] else '',
                                    ('représenté(e) par ' + p['representant']) if p.get('representant') else '') if x)
    if not p.get('id'):   # mention sans identifiant de projection : ajout par le formulaire de 5.6.98 (rôle toujours à choisir)
        form = _formulaire(_api(did, 'parties'), '<input type="hidden" name="nom" value="%s"><input type="hidden" name="qualite" value="%s">'
                           '<label class="d98-champ d98-inline">Rôle<select name="role" required>%s</select></label>%s%s' % (
                               e(p['nom'], quote=True), e(p['qualite'], quote=True), options_roles(p['role']), _erreur('role'), _erreur('nom')),
                           'Ajouter au dossier', 'd98-form-ligne')
        return '<li class="d98-propose"><strong>%s</strong>%s%s</li>' % (e(p['nom']), (' <span class="ax-muted">— %s</span>' % info) if info else '', form)
    api = _api109(did, 'candidats', p['id'])
    version = '<input type="hidden" name="version" value="%d">' % int(p.get('version') or 0)
    accepter = _formulaire(api, '<input type="hidden" name="decision" value="accepter">' + version
                           + '<label class="d98-champ d98-inline">Rôle<select name="role" required>%s</select></label>%s' % (options_roles(p['role']), _erreur('role'))
                           + '<details class="d98-editer"><summary>Corriger le nom ou la qualité avant d’ajouter</summary><div class="d98-grille">%s%s</div></details>' % (
                               _champ('Nom ou dénomination', 'nom', p['nom'], 160),
                               _champ('Qualité dans l’acte', 'qualite', p['qualite'], 160).replace('name="qualite"', 'name="qualite" list="d98-qualites"', 1)),
                           'Ajouter au dossier', 'd98-form-ligne')
    cibles = p.get('doublons') or [{'id': x['id'], 'nom': x['nom'], 'role': x['role'], 'motif': ''} for x in inscrites
                                   if x['role'] not in F.ROLES_AVOCATS and x['role'] != 'juridiction']
    fusion = ''
    if cibles:
        fusion = _formulaire(api, '<input type="hidden" name="decision" value="fusionner">' + version
                             + '<label class="d98-champ d98-inline">Même personne que<select name="partie_id">%s</select></label>%s' % (''.join(
                                 '<option value="%s">%s (%s)</option>' % (e(x['id'], quote=True), e(x['nom']), e(D.ROLES_PARTIES.get(x['role'], x['role']))) for x in cibles[:30]),
                                 _erreur('partie_id')), 'Rattacher sans modifier sa fiche', 'd98-form-ligne')
        fusion = '<details class="d98-editer"><summary>Fusionner avec une personne inscrite</summary>%s</details>' % fusion
    ecarter = _formulaire(api, '<input type="hidden" name="decision" value="ecarter">' + version, 'Écarter', 'd98-retirer',
                          'Écarter cette mention ? Elle ne sera plus proposée tant que sa source ne change pas.')
    avis = ''
    if p.get('nettoyage'):
        avis += '<p class="d109-avis" role="note"><strong>Nettoyage proposé.</strong> %s Rien n’est retiré sans votre décision.</p>' % e(p['nettoyage'])
    if p.get('revu'):
        avis += '<p class="d109-avis" role="note">%s</p>' % e(p['revu'])
    if p.get('doublons'):
        avis += '<p class="d109-avis" role="note">Doublon possible : %s. Aucune fusion n’est faite d’office.</p>' % e(' ; '.join(
            '%s (%s)' % (x['nom'], x.get('motif') or 'même nom') for x in p['doublons'][:3]))
    return ('<li class="d98-propose%s"><div class="d98-partie-tete"><strong>%s</strong>%s</div><p class="d79-sources">%s</p>%s<div class="d98-gestes">%s%s%s</div></li>') % (
        ' d109-nettoyage' if p.get('nettoyage') else '', e(p['nom']), (' <span class="ax-muted">— %s</span>' % info) if info else '',
        _sources_html(prefix, did, p.get('sources')) or '<span class="ax-muted">sans source vérifiable</span>', avis, accepter, fusion, ecarter)


def _proposees_html(did, proposees, prefix='', inscrites=()):
    if proposees is None:
        corps = '<p class="ax-muted">Lecture des parties citées dans les pièces indisponible pour le moment.</p>'
    elif not proposees:
        corps = '<p class="ax-muted">Aucune autre partie lue dans les pièces. « ↻ Actualiser » relit les en-têtes des actes.</p>'
    else:
        corps = '<ul class="d98-liste">%s</ul>' % ''.join(_proposee_ligne(did, p, prefix, inscrites) for p in proposees)
    relire = ''
    if proposees is not None and any(p.get('id') for p in proposees) or (proposees == [] and inscrites):
        relire = _formulaire(_api109(did, 'relire'), '', 'Relire les actes et contrats', 'd98-form-ligne')
    return ('<section class="d98-groupe d98-proposees d79-card" aria-labelledby="d98-g-proposees"><div class="d79-card-head"><h2 id="d98-g-proposees">'
            'Proposées, à valider</h2></div><p class="ax-muted">Parties lues dans les pièces du dossier. Elles ne sont jamais ajoutées automatiquement : '
            'vérifiez le rôle puis « Ajouter au dossier ».</p>%s%s</section>') % (corps, relire)


def _proposees_lecture(desk, m):
    """Dossier hors registre : parties lues dans les pièces, en lecture seule."""
    try:
        from .resume5659 import representation
        reps = representation(desk, m['id'])
    except Exception:   # noqa: BLE001
        return '<p class="ax-muted">Parties lues dans les pièces indisponibles pour le moment.</p>'
    if not reps:
        return '<p class="ax-muted">Aucune partie lue dans les pièces.</p>'
    return '<section class="d79-card"><div class="d79-card-head"><h2>Parties lues dans les pièces</h2></div><ul class="d98-liste">%s</ul></section>' % ''.join(
        '<li><strong>%s</strong>%s</li>' % (e(p['nom']), ' <span class="ax-muted">— %s</span>' % e(' · '.join(x for x in (p['qualite'], p['conseil']) if x))
                                            if (p['qualite'] or p['conseil']) else '') for p in reps[:60])


# ------------------------------------------------------------------------------------------------ 5.6.109 (F04) : fiche Conseil / Contentieux
LIBELLES_STATUT = {'propose': 'proposé, à vérifier', 'a_verifier': 'à vérifier', 'rapportee': 'mentionnée, original non examiné'}
DATES_ACTE = {'acte': 'acte du', 'signification': 'signifié le', 'notification': 'notifié le', 'depot': 'déposé le', 'reception': 'reçu le',
              'audience': 'audience du', 'evenement': 'le'}
PAR_CARTE = 8   # éléments montrés par carte ; la suite est dans la vue détaillée (les cartes repliées ne chargent rien de plus)


def _valeur_html(c):
    v = c.get('valeur')
    if v is None:
        return '<span class="ax-muted">Non renseigné</span>'
    if isinstance(v, list):
        return '<ul class="d109-liste">%s</ul>' % ''.join('<li>%s</li>' % e(str(x)) for x in v)
    if isinstance(v, bool):
        return 'Oui' if v else 'Non'
    if isinstance(v, dict) and 'cents' in v:
        from .honoraires5653 import fmt
        return e(fmt(v['cents']))
    if c['type'] == 'date':
        return e(_date_fr(v))
    return e(str(v)).replace('\n', '<br>')


def _saisie_html(c, nom='valeur', valeur=None, parties=()):
    """Champ de saisie d'une valeur de la fiche selon son type (liste fermée de types ; aucune valeur n'est rendue sans échappement)."""
    v = c.get('valeur') if valeur is None else valeur
    if c['type'] == 'liste':
        texte = '\n'.join(str(x) for x in v) if isinstance(v, list) else str(v or '')
        return '<textarea name="%s" rows="4" maxlength="12000" aria-label="%s">%s</textarea>' % (nom, e(c['libelle'], quote=True), e(texte))
    if c['type'] == 'date':
        return '<input type="date" name="%s" value="%s" aria-label="%s">' % (nom, e(str(v or ''), quote=True), e(c['libelle'], quote=True))
    if c['type'] == 'montant':
        cents = v.get('cents') if isinstance(v, dict) else None
        texte = '' if cents is None else '%d,%02d' % (cents // 100, cents % 100)
        return '<input name="%s" value="%s" inputmode="decimal" maxlength="20" aria-label="%s (en euros)">' % (nom, e(texte, quote=True), e(c['libelle'], quote=True))
    if c['type'] == 'oui_non':
        return '<select name="%s" aria-label="%s"><option value=""></option><option value="oui"%s>Oui</option><option value="non"%s>Non</option></select>' % (
            nom, e(c['libelle'], quote=True), ' selected' if v is True else '', ' selected' if v is False else '')
    if c['type'] in ('choix', 'personne'):
        choix = [(x, x) for x in c.get('choix') or []] if c['type'] == 'choix' else [(p['id'], p['nom']) for p in parties]
        return '<select name="%s" aria-label="%s"><option value=""></option>%s</select>' % (nom, e(c['libelle'], quote=True), ''.join(
            '<option value="%s"%s>%s</option>' % (e(k, quote=True), ' selected' if k == v else '', e(lib)) for k, lib in choix))
    return '<textarea name="%s" rows="3" maxlength="4000" aria-label="%s">%s</textarea>' % (nom, e(c['libelle'], quote=True), e(str(v or '')))


def _champ_fiche_html(prefix, did, c, parties=(), titre='h3'):
    """Une information de la fiche : valeur, origine et source ; Modifier ; propositions en attente (ancienne valeur, proposition,
    justification, source) avec Accepter, Corriger, Écarter. Une valeur saisie par l'avocat n'est remplacée qu'après confirmation expresse."""
    origine = ''
    if c['version']:
        origine = '<p class="d109-origine ax-muted">%s%s%s %s</p>' % (
            e(c['origine_libelle'] or c['origine']), (' · ' + e(c['modifie_par'])) if c['modifie_par'] else '',
            (' · ' + e(_date_fr(c['modifie_le']))) if c['modifie_le'] else '', _sources_html(prefix, did, c['sources']))
    alerte = '<p class="d109-avis" role="note">Source retirée ou déplacée : à vérifier.</p>' if c.get('a_verifier') else ''
    modifier = '<details class="d98-editer"><summary>Modifier</summary>%s</details>' % _formulaire(
        _api109(did, 'valeurs', c['id']), '<input type="hidden" name="version" value="%d"><label class="d98-champ d98-large">%s%s%s%s</label>' % (
            c['version'], e(c['libelle']), _saisie_html(c, parties=parties), ('<span class="d97-aide">%s</span>' % e(c['aide'])) if c.get('aide') else '',
            _erreur('valeur')), 'Enregistrer')
    props = ''
    for p in c['propositions']:
        api = _api109(did, 'propositions', p['id'])
        cache = ('<input type="hidden" name="version_attendue" value="%d"><input type="hidden" name="base_version" value="%d">' % (c['version'], p['base_version']))
        remplacer = ('<label class="d98-case"><input type="checkbox" name="remplacement" value="1"> Remplacer la valeur saisie par l’avocat</label>%s' % _erreur('remplacement')) \
            if c['origine'] == 'avocat' else ''
        accepter = _formulaire(api, '<input type="hidden" name="decision" value="accepter">' + cache + remplacer + _erreur('valeur'), 'Accepter', 'd98-form-ligne') \
            if p['verifiable'] else '<p class="ax-muted">Source retirée : cette proposition ne peut plus être acceptée telle quelle.</p>'
        corriger = '<details class="d98-editer"><summary>Corriger</summary>%s</details>' % _formulaire(
            api, '<input type="hidden" name="decision" value="corriger">' + cache + '<label class="d98-champ d98-large">Valeur corrigée%s%s</label>' % (
                _saisie_html(c, 'valeur_corrigee', p['valeur'], parties), _erreur('valeur_corrigee')), 'Enregistrer la correction')
        ecarter = _formulaire(api, '<input type="hidden" name="decision" value="ecarter">', 'Écarter', 'd98-form-ligne')
        props += ('<div class="d109-proposition" role="group" aria-label="Proposition à décider"><p><span class="d109-statut">Proposition à vérifier</span> %s</p>'
                  '<dl class="d109-comparaison"><div><dt>Valeur actuelle</dt><dd>%s</dd></div><div><dt>Proposition</dt><dd>%s</dd></div></dl>%s'
                  '<div class="d98-gestes">%s%s%s</div></div>') % (
            _sources_html(prefix, did, p['sources']), _valeur_html(c), _valeur_html({**c, 'valeur': p['valeur']}),
            ('<p class="ax-muted">%s</p>' % e(p['justification'])) if p['justification'] else '', accepter, corriger, ecarter)
    return '<div class="d109-champ"><%s>%s</%s><div class="d109-valeur">%s</div>%s%s%s%s</div>' % (
        titre, e(c['libelle']), titre, _valeur_html(c), origine, alerte, modifier, props)


def _element_html(prefix, did, x):
    """Un élément sourcé : attribution (« Selon … »), texte, force probante, statut de vérification et liens vers la source."""
    date = ''
    if x.get('dates'):
        date = ' · '.join('%s %s' % (DATES_ACTE.get(k, k), _date_fr(v) or v) for k, v in sorted(x['dates'].items()))
    elif x.get('date'):
        date = _date_fr(x['date']) or x['date']
    attribution = ('<span class="d109-attribution">Selon %s</span> : ' % e(x['auteur'])) if x.get('attribue') or (
        x.get('type') == 'fait' and x.get('force_probante') in ('allegue', 'conteste') and x.get('auteur')) else ''
    marques = []
    if x.get('force_libelle') and x.get('type') in ('fait', 'demande', 'montant', 'argument'):
        marques.append('<span class="d109-force">%s</span>' % e(x['force_libelle']))
    if x.get('statut') in LIBELLES_STATUT:
        marques.append('<span class="d109-statut">%s</span>' % e(LIBELLES_STATUT[x['statut']]))
    elif x.get('a_verifier'):
        marques.append('<span class="d109-statut">à vérifier</span>')
    if x.get('libelle'):
        marques.insert(0, '<span class="d51-kind">%s</span>' % e(x['libelle']))
    return '<li class="d109-item%s">%s%s%s %s %s</li>' % (
        ' d109-a-verifier' if x.get('a_verifier') else '', ('<span class="d79-date">%s</span> ' % e(date)) if date else '', attribution, e(str(x['texte'])[:600]),
        ' '.join(marques), _sources_html(prefix, did, x.get('sources')) or '<span class="d51-src ax-muted">sans source vérifiable</span>')


def _liste_elements(prefix, did, elements, vide, suite=''):
    if not elements:
        return '<p class="ax-muted">%s</p>' % e(vide)
    plus = ''
    if len(elements) > PAR_CARTE:
        plus = '<p class="ax-muted">%d autre(s) élément(s)%s.</p>' % (len(elements) - PAR_CARTE, (' : <a href="%s">voir tout</a>' % suite) if suite else '')
    return '<ul class="d79-list d109-elements">%s</ul>%s' % (''.join(_element_html(prefix, did, x) for x in elements[:PAR_CARTE]), plus)


def _carte_fiche(carte, corps, compte=None):
    """Carte repliable de la fiche ; le compteur reste lisible quand elle est repliée."""
    return ('<details class="d109-carte" id="d109-%s"%s><summary><span class="d109-titre">%s</span>%s</summary><div class="d109-corps">%s</div></details>') % (
        e(carte['id'], quote=True), '' if carte['repliee'] else ' open', e(carte['libelle']),
        (' <span class="d98-compte">%d</span>' % compte) if compte is not None else '', corps)


def _personnaliser_html(f, peut_regler):
    """« Personnaliser la fiche » : ordre, cartes masquées et repliées pour SON compte ; « Revenir à la présentation du cabinet » ; pour qui
    règle le cabinet, le modèle de fiche et les champs métier. Aucune préférence ne supprime une donnée."""
    pres = f['presentation']

    def lignes():
        # libellés fixes (traduisibles) : l'en-tête de ligne donne le nom de la carte aux lecteurs d'écran
        return ''.join('<tr><th scope="row">%s</th><td><input type="number" name="carte_%s_ordre" value="%d" min="1" max="20" aria-label="Rang de la carte"></td>'
                       '<td><input type="checkbox" name="carte_%s_masquee"%s%s aria-label="Masquer la carte"></td>'
                       '<td><input type="checkbox" name="carte_%s_repliee"%s aria-label="Replier la carte"></td></tr>' % (
                           e(c['libelle']), c['id'], n + 1, c['id'], ' checked' if c['masquee'] else '', '' if c['masquable'] else ' disabled',
                           c['id'], ' checked' if c['repliee'] else '') for n, c in enumerate(pres['cartes']))
    table = ('<table class="d97-table d109-cartes"><caption class="sr-only">Cartes de la fiche</caption><thead><tr><th scope="col">Carte</th><th scope="col">Rang</th>'
             '<th scope="col">Masquer</th><th scope="col">Replier</th></tr></thead><tbody>%s</tbody></table>')
    cache = '<input type="hidden" name="profil" value="%s">' % e(f['profil'], quote=True)
    perso = _formulaire('m56109/fiche/presentation', cache + '<input type="hidden" name="portee" value="utilisateur"><input type="hidden" name="version" value="%d">' % pres['version']
                        + table % lignes(), 'Enregistrer ma présentation')
    retour = _formulaire('m56109/fiche/presentation', cache + '<input type="hidden" name="portee" value="utilisateur"><input type="hidden" name="version" value="%d">'
                         '<input type="hidden" name="reinitialiser" value="1">' % pres['version'], 'Revenir à la présentation du cabinet', 'd98-form-ligne') if pres['version'] else ''
    cabinet = ''
    if peut_regler:
        persos = [c for c in f['champs'].values() if c['carte'] == 'champs']
        rangs = ''.join('<div class="d98-grille">%s%s<label class="d98-champ">Type<select name="champ_%d_type">%s</select></label>%s%s</div>' % (
            '<input type="hidden" name="champ_%d_id" value="%s">' % (n, e(c.get('id', ''), quote=True)), _champ('Libellé du champ', 'champ_%d_libelle' % n, c.get('libelle', ''), 80),
            n, _options(_types_champ(), c.get('type', 'texte')), _champ('Aide', 'champ_%d_aide' % n, c.get('aide', ''), 200),
            _champ('Valeurs possibles (choix), séparées par « ; »', 'champ_%d_choix' % n, ' ; '.join(c.get('choix') or []), 600))
            for n, c in enumerate((persos + [{}, {}])[:12], 1))
        cabinet = ('<details class="d98-editer"><summary>Modèle de fiche du cabinet et champs métier</summary><p class="ax-muted">Présentation proposée à tout le cabinet '
                   'pour ce profil, et champs propres au cabinet (texte, date, montant, choix, oui / non, personne, liste). Vider le libellé d’un champ le retire du '
                   'modèle ; les valeurs déjà saisies dans les dossiers sont conservées.</p>%s</details>') % _formulaire(
            'm56109/fiche/presentation', cache + '<input type="hidden" name="portee" value="cabinet"><input type="hidden" name="version" value="%d">' % f.get('version_cabinet', 0)
            + table % lignes() + rangs, 'Enregistrer le modèle du cabinet')
    return ('<details class="d109-perso"><summary class="ax-btn ghost">Personnaliser la fiche</summary><p class="ax-muted">Ces réglages ne changent que votre affichage : '
            'aucune donnée du dossier n’est modifiée ni supprimée, et les éléments à vérifier restent comptés dans la première carte.</p>%s%s%s</details>') % (perso, retour, cabinet)


def _types_champ():
    from .projection56109 import TYPES
    return TYPES


def fiche_html(desk, acteur, prefix, m, args=None):
    """5.6.109 (F04) : fiche du dossier lue par la projection commune (``projection56109.fiche``), dans la Synthèse. Première carte : client,
    responsable, type, quatre questions (mission, situation, élément nouveau, décision attendue), prochaine échéance CONFIRMÉE ; puis les
    cartes du profil Contentieux, Conseil ou Mixte. Aucun appel à un modèle, aucune écriture. Dossier hors registre : rien (ancienne vue)."""
    from . import projection56109 as P
    args = args or {}
    try:
        f = P.fiche(desk, acteur, m['id'], str(args.get('profil') or '') or None)
    except Stop as ex:
        if str(ex) != 'introuvable':
            raise
        return ''
    did, profil, b = f['dossier'], f['profil'], f['en_bref']
    parties = [p for p in f['parties_inscrites'] if p['partie']]
    cartes = {c['id']: c for c in f['presentation']['cartes']}
    a = F._acteur(acteur)
    peut_regler = a.role == 'administrateur'
    try:
        f['version_cabinet'] = (P._presentation_brute(desk, 'cabinet', '', profil) or {'version': 0})['version']
        if not peut_regler:
            P._peut_regler(desk, a)
            peut_regler = True
    except Stop:
        pass
    sections, champs = f['sections'], f['champs']
    lien = lambda vue, ancre='': e(prefix + '/dossier?' + urlencode({'id': did, 'vue': vue}) + ancre, quote=True)
    # --- première carte
    faits_bref = [('Client représenté', e(b['client']) if b['client'] else '<span class="ax-muted">à identifier</span>'),
                  ('Responsable', e(b['responsable']) if b['responsable'] else '<span class="ax-muted">non attribué</span>'),
                  ('Type', e(b['type_libelle'])),
                  ('Prochaine échéance confirmée', ('%s — %s' % (e(_date_fr(b['echeance_confirmee']['date'])), e(b['echeance_confirmee']['libelle']))) if b['echeance_confirmee']
                   else '<span class="ax-muted">aucune échéance confirmée à venir</span>'),
                  ('Dernier changement', ('%s — %s' % (e(_date_fr(b['dernier_changement']['date'])), e(b['dernier_changement']['libelle']))) if b['dernier_changement']
                   else '<span class="ax-muted">aucun changement enregistré</span>')]
    alertes = []
    if b['a_verifier']:
        alertes.append('<a href="%s">%d personne(s) lue(s) dans les pièces à vérifier</a>' % (lien('parties', '#d98-g-proposees'), b['a_verifier']))
    if b['propositions']:
        alertes.append('%d proposition(s) à décider ci-dessous' % b['propositions'])
    if b['dates_a_verifier']:
        alertes.append('<a href="%s">%d date(s) proposée(s), non confirmée(s)</a>' % (e(prefix + '/echeances?' + urlencode({'matter': did}), quote=True), b['dates_a_verifier']))
    objectif = ''.join('<p>%s %s</p>' % (e(o['texte']), _sources_html(prefix, did, o['sources'])) for o in b['objectif'])
    bref = ('<dl class="d109-bref">%s</dl>%s<div class="d109-questions">%s</div>%s') % (
        ''.join('<div><dt>%s</dt><dd>%s</dd></div>' % (e(t), v) for t, v in faits_bref),
        ('<p class="d109-avis" role="status">%s</p>' % ' · '.join(alertes)) if alertes else '',
        ''.join(_champ_fiche_html(prefix, did, c, parties) for c in b['questions']),
        ('<div class="d109-champ"><h3>Objectif du client</h3>%s</div>' % objectif) if objectif else '')
    corps = {'en_bref': bref}
    # --- parties et candidats
    lignes = ''.join('<li><strong>%s</strong> <span class="d98-role">%s</span>%s%s</li>' % (
        e(p['nom']), e(p['role_libelle']), (' — ' + e(p['qualite'])) if p['qualite'] else '',
        ''.join(' · <span class="ax-muted">%s : %s%s</span>' % (e(r['cadre'] or 'cadre à préciser'), e(r['qualite']), '' if r['origine'] == 'avocat' else ' (lu, à vérifier)')
                for r in p['roles_par_instance'][:4])) for p in parties[:12])
    position = _champ_fiche_html(prefix, did, champs['position_client'], parties) if 'position_client' in champs else ''
    corps['parties'] = ((('<ul class="d79-list">%s</ul>' % lignes) if lignes else '<p class="ax-muted">Aucune partie inscrite.</p>') + position
                        + '<p><a href="%s">Gérer les parties, leurs qualités par instance et leurs avocats</a></p>' % lien('parties'))
    cands = f['candidats_a_verifier']
    corps['candidats'] = ((('<ul class="d79-list">%s</ul>' % ''.join('<li>%s%s%s</li>' % (
        e(c['nom']), (' — ' + e(c['qualite'])) if c['qualite'] else '', ' <span class="d109-statut">nettoyage proposé</span>' if c['nettoyage'] else '') for c in cands[:PAR_CARTE]))
        if cands else '<p class="ax-muted">Aucune mention en attente.</p>')
        + '<p class="ax-muted">Ces personnes ne sont pas des parties établies : elles n’entrent ni dans la synthèse ni dans les récits tant qu’elles ne sont pas '
          'validées.</p><p><a href="%s">Accepter, corriger, fusionner ou écarter</a></p>' % lien('parties', '#d98-g-proposees'))
    # --- cartes de champs
    corps['operation'] = ''.join(_champ_fiche_html(prefix, did, c, parties) for c in champs.values() if c['carte'] == 'operation')
    persos = [c for c in champs.values() if c['carte'] == 'champs']
    corps['champs'] = ''.join(_champ_fiche_html(prefix, did, c, parties) for c in persos) or \
        '<p class="ax-muted">Aucun champ propre au cabinet pour ce profil. « Personnaliser la fiche » permet d’en définir.</p>'
    # --- éléments sourcés, par fonction
    infos = lien('informations')
    corps['contrats'] = _liste_elements(prefix, did, sections.get('contrats', []), 'Aucun contrat identifié dans les fichiers du dossier.', lien('fichiers'))
    corps['faits'] = _liste_elements(prefix, did, sections.get('faits', []), 'Aucun fait constaté dans une pièce, corroboré ou retenu par une décision.', infos)
    corps['allegations'] = ('<p class="ax-muted">Chaque affirmation reste attribuée à son auteur ; la valider signifie que la source est bien reprise, pas que son '
                            'contenu est établi.</p>' + _liste_elements(prefix, did, sections.get('allegations', []), 'Aucune affirmation relevée.', infos))
    corps['pretentions'] = _liste_elements(prefix, did, sections.get('pretentions', []), 'Aucune demande relevée.', infos)
    corps['arguments'] = _liste_elements(prefix, did, sections.get('arguments', []), 'Aucun argument relevé.', infos)
    corps['procedure'] = ('<p class="ax-muted">Dates d’acte, de signification, de dépôt et d’audience distinguées ; une date lue dans un acte n’est pas une échéance '
                          'confirmée.</p>' + _liste_elements(prefix, did, sections.get('procedure', []), 'Aucun acte de procédure relevé.', lien('faits')))
    corps['decisions'] = _liste_elements(prefix, did, sections.get('decisions', []), 'Aucune décision de justice relevée.', lien('faits'))
    corps['decisions_cabinet'] = _liste_elements(prefix, did, sections.get('decisions_cabinet', []), 'Aucune décision du cabinet enregistrée.', lien('faits', '#d51-decisions'))
    comptes = {**f['compteurs'], 'parties': len(parties), 'candidats': len(cands), 'operation': None, 'champs': len(persos), 'en_bref': None}
    blocs = ''
    for c in f['presentation']['cartes']:
        if c['masquee'] or c['id'] not in corps:
            continue
        if c['id'] == 'en_bref':
            blocs += '<div class="d109-carte d109-premiere" id="d109-en_bref">%s</div>' % corps['en_bref']
        else:
            blocs += _carte_fiche(c, corps[c['id']], comptes.get(c['id']))
    profils = _formulaire(_api109(did, 'profil'), '<input type="hidden" name="version" value="%d"><label class="d98-champ d98-inline">Profil de la fiche<select name="profil">%s'
                          '</select></label>%s' % (f['version_profil'], _options(f['profils'], f['profil_dossier']), _erreur('profil')), 'Appliquer', 'd98-form-ligne')
    cov = f['couverture']
    pied = '<p class="d109-pied ax-muted">%d document(s) lu(s) sur %d identifié(s) · fiche calculée sans rédaction par l’IA.%s</p>' % (
        cov['lus'], cov['documents'], ''.join(' ' + e(x) for x in cov['limites']))
    return ('<section class="d79-card d109-fiche" id="d109-fiche" aria-labelledby="d109-fiche-t"><div class="d79-card-head"><h2 id="d109-fiche-t">Fiche du dossier '
            '<span class="d27-badge">%s</span></h2></div><datalist id="d98-qualites">%s</datalist>%s<div class="d109-outils">%s%s</div>%s</section>') % (
        e(b['type_libelle']), ''.join('<option value="%s">' % e(q, quote=True) for q in F.QUALITES), blocs, profils, _personnaliser_html(f, peut_regler), pied)


# ------------------------------------------------------------------------------------------------ Juridiction
def _corps_instance(i=None, principale_figee=False):
    i = i or {}
    if principale_figee:
        principale = ('<input type="hidden" name="principale" value="1"><input type="hidden" name="version_dossier" value="%d">'
                      '<p class="d98-champ d98-large ax-muted">Instance principale : sa juridiction et son RG sont ceux du dossier.</p>' % int(i.get('version_dossier') or 0))
    else:
        principale = '<label class="d98-case d98-large"><input type="checkbox" name="principale" value="1"%s> Instance principale (juridiction et RG du dossier)</label>%s' % (
            ' checked' if i.get('principale') else '', _erreur('principale'))
    return ('<div class="d98-grille">'
            + '<label class="d98-champ">Degré<select name="degre">%s</select>%s</label>' % (_options(F.DEGRES, i.get('degre') or 'premiere_instance'), _erreur('degre'))
            + _champ('Juridiction', 'juridiction', i.get('juridiction'), 160, aide='Ex. Tribunal judiciaire de Valbrune')
            + _champ('Chambre ou formation', 'chambre', i.get('chambre'), 160)
            + _champ('Numéro RG', 'rg', i.get('rg'), 40, aide='Ex. 24/01234')
            + _champ('Numéro Portalis', 'portalis', i.get('portalis'), 40)
            + '<label class="d98-champ">Nature<select name="nature">%s</select>%s</label>' % (_options(F.NATURES_INSTANCE, i.get('nature') or ''), _erreur('nature'))
            + _champ('Date de saisine', 'saisine', i.get('saisine'), 10, 'date')
            + _champ('Magistrat (facultatif)', 'magistrat', i.get('magistrat'), 160)
            + _champ('Greffe (contact)', 'greffe', i.get('greffe'), 300, large=True, aide='Téléphone, courriel ou adresse du greffe')
            + '<label class="d98-champ">État<select name="etat">%s</select>%s</label>' % (_options(F.ETATS_INSTANCE, i.get('etat') or 'en_cours'), _erreur('etat'))
            + principale + '</div>')


def _carte_instance(did, i):
    lignes = [('Chambre ou formation', i['chambre']), ('Numéro RG', i['rg']), ('Numéro Portalis', i['portalis']), ('Nature', i['nature_libelle']),
              ('Saisine', _date_fr(i['saisine'])), ('Magistrat', i['magistrat']), ('Greffe', i['greffe']), ('État', i['etat_libelle'])]
    dl = ''.join('<div><dt>%s</dt><dd>%s</dd></div>' % (e(t), e(v) if v else '<span class="ax-muted">—</span>') for t, v in lignes)
    modifier = '<details class="d98-editer"><summary>Modifier</summary>%s</details>' % _formulaire(
        _api(did, 'instances', i['id']), '<input type="hidden" name="version" value="%d">' % i['version'] + _corps_instance(i, i['principale']), 'Enregistrer')
    retirer = '' if i['principale'] else _formulaire(_api(did, 'instances', i['id'], 'retirer'), '<input type="hidden" name="version" value="%d">' % i['version'],
                                                    'Retirer', 'd98-retirer', 'Retirer cette instance du dossier ?')
    return ('<article class="d98-instance%s"><h3>%s — %s%s</h3><dl class="d98-dl">%s</dl><div class="d98-gestes">%s%s</div></article>') % (
        ' d98-principale' if i['principale'] else '', e(i['degre_libelle']), e(i['juridiction'] or 'Juridiction à préciser'),
        ' <span class="d27-badge">principale</span>' if i['principale'] else '', dl, modifier, retirer)


def _audiences(desk, mid):
    from datetime import date
    try:
        from . import audiences5688
        rows = desk.db.execute("SELECT juridiction,chambre,type_audience,starts,statut,avocat FROM audiences5688 WHERE matter=? AND statut IN ('a_venir','renvoyee') "
                               "AND starts>=? ORDER BY starts LIMIT 3", (str(mid), date.today().isoformat())).fetchall()
        return [{**dict(r), 'type': audiences5688.TYPES.get(r['type_audience'], r['type_audience'])} for r in rows]
    except Exception:   # noqa: BLE001 — table jamais initialisée : aucune audience enregistrée
        return []


def _prochaines_html(desk, prefix, mid):
    auds = _audiences(desk, mid)
    if auds:
        corps = '<ul class="d79-list">%s</ul>' % ''.join('<li><strong>%s%s</strong> — %s%s</li>' % (
            e(_date_fr(a['starts'])), (' ' + e(str(a['starts'])[11:16])) if len(str(a['starts'])) > 15 else '', e(a['type']),
            (' · ' + e(' · '.join(x for x in (a['juridiction'], a['chambre']) if x))) if (a['juridiction'] or a['chambre']) else '') for a in auds)
    else:
        try:
            from .resume5659 import dates
            confirmees, proposees = dates(desk, mid)
        except Exception:   # noqa: BLE001
            confirmees, proposees = None, 0
        if confirmees is None:
            corps = '<p class="ax-muted">Agenda indisponible pour le moment.</p>'
        elif confirmees:
            corps = '<p class="ax-muted">Aucune audience enregistrée ; prochaines dates confirmées :</p><ul class="d79-list">%s</ul>' % ''.join(
                '<li><strong>%s</strong> — %s</li>' % (e(_date_fr(x['date'])), e(x['titre'])) for x in confirmees[:3])
        else:
            corps = '<p class="ax-muted">Aucune audience ni date confirmée à venir.</p>'
        if proposees:
            corps += '<p><a href="%s">%d date(s) proposée(s) à vérifier</a></p>' % (e(prefix + '/echeances?' + urlencode({'matter': mid}), quote=True), proposees)
    return corps + '<p><a href="%s">Agenda et tâches du dossier</a></p>' % _q(prefix, mid, 'agenda')


def _actes_html(desk, prefix, mid):
    try:
        from .echeances450 import actes_consignes
        actes = actes_consignes(desk, str(mid), 5)
    except Exception:   # noqa: BLE001
        return '<p class="ax-muted">Registre des actes indisponible pour le moment.</p>'
    from .fiche460 import doc_href
    lignes = ''.join('<li><span class="d79-date">%s</span> %s%s</li>' % (
        e(_date_fr(a['created'])), e(a['type_libelle']),
        (' <a class="d51-src" href="%s" data-d79-doc="%s">%s</a>' % (e(prefix + doc_href(a['path'], mid), quote=True), e(a['path'], quote=True),
                                                                     e(a['path'].rsplit('/', 1)[-1]))) if a.get('path') else '') for a in actes)
    corps = ('<ul class="d79-list">%s</ul>' % lignes) if lignes else '<p class="ax-muted">Aucun acte consigné.</p>'
    return corps + '<p><a href="%s">Registre procédural complet (échéances, actes, dépôts)</a></p>' % _q(prefix, mid, 'faits')


def juridiction_html(desk, acteur, prefix, m):
    mid = m['id']
    blocs = ('<div class="d79-grid2"><section class="d79-card"><div class="d79-card-head"><h2>Prochaine audience</h2></div>%s</section>'
             '<section class="d79-card"><div class="d79-card-head"><h2>Derniers actes</h2></div>%s</section></div>') % (
        _prochaines_html(desk, prefix, mid), _actes_html(desk, prefix, mid))
    from .suites56109 import montage_fiche as suites56109   # 5.6.109 (lot G3, G05) : point de montage unique du bloc « Suites et exécution » (ne lève jamais)
    blocs += suites56109(desk, acteur, prefix, m)
    try:
        data = F.instances(desk, acteur, mid)
    except Stop as ex:
        if str(ex) != 'introuvable':
            raise
        return '<div class="d98" id="d98-juridiction">' + _hors_registre(prefix, acteur) + blocs + '</div>'
    did = data['dossier']
    cartes = ''.join(_carte_instance(did, i) for i in data['instances']) or \
        '<p class="ax-muted">Aucune instance. Ajoutez la juridiction saisie : la première instance devient l’instance principale du dossier.</p>'
    ajout = '<details class="d98-ajout"%s><summary class="ax-btn ghost">Ajouter une instance</summary>%s</details>' % (
        '' if data['instances'] else ' open', _formulaire(_api(did, 'instances'), _corps_instance(), 'Ajouter'))
    return ('<div class="d98" id="d98-juridiction"><section class="d79-card" aria-labelledby="d98-inst-t"><div class="d79-card-head"><h2 id="d98-inst-t">Instances</h2></div>'
            '<p class="ax-muted">Première instance, appel, cassation, référé… L’instance principale donne la juridiction et le RG affichés partout dans le dossier.</p>'
            '%s%s</section>%s%s</div>') % (cartes, ajout, blocs, _lues_html(desk, did, data['instances']))


def _lues_html(desk, did, instances):
    """Procédures lues dans les pièces (``modele5651``) sans instance au même RG : proposées, jamais ajoutées."""
    try:
        from . import modele5651
        procs = modele5651.procedures(desk, did)
    except Exception:   # noqa: BLE001
        return ''
    rgs = {D._plier(i['rg']) for i in instances if i['rg']}
    lignes = []
    for p in procs:
        if p.get('statut') == 'citee' or not (p.get('juridiction') or p.get('rg')) or (p.get('rg') and D._plier(p['rg']) in rgs):
            continue
        form = _formulaire(_api(did, 'instances'), ''.join('<input type="hidden" name="%s" value="%s">' % (k, e(str(p.get(k) or '')[:160], quote=True))
                                                          for k in ('juridiction', 'chambre', 'rg')) + _erreur('juridiction') + _erreur('rg'),
                           'Ajouter comme instance', 'd98-form-ligne')
        lignes.append('<li class="d98-propose"><strong>%s</strong>%s%s</li>' % (
            e(p.get('juridiction') or 'Juridiction à préciser'), (' <span class="ax-muted">— RG %s</span>' % e(p['rg'])) if p.get('rg') else '', form))
    if not lignes:
        return ''
    return ('<section class="d79-card d98-proposees"><div class="d79-card-head"><h2>Procédures lues dans les pièces</h2></div>'
            '<p class="ax-muted">À valider : elles ne deviennent des instances du dossier qu’après « Ajouter comme instance ».</p><ul class="d98-liste">%s</ul></section>') % ''.join(lignes[:10])


# ------------------------------------------------------------------------------------------------ Fichiers
SOUS_VUES = (('type', 'Par type'), ('repertoire', 'Par répertoire'))


def fichiers_html(desk, auth, acteur, prefix, m, args, par_type):
    sous = str(args.get('sous') or 'type')
    sous = sous if sous in dict(SOUS_VUES) else 'type'
    onglets = ''.join('<a role="tab" id="d98-sous-%s" href="%s" aria-selected="%s" aria-controls="d98-fichiers-panneau">%s</a>' % (
        k, _q(prefix, m['id'], 'fichiers', sous=k if k != 'type' else ''), 'true' if k == sous else 'false', e(v)) for k, v in SOUS_VUES)
    corps = par_type() if sous == 'type' else repertoire_html(desk, acteur, prefix, m, args)
    from .reanalyse56109 import montage_fiche as analyse56109   # 5.6.109 (lot A, F03) : point de montage unique du bloc « Analyse du dossier » (rendu par reanalyse56109 ; ne lève jamais)
    return analyse56109(desk, acteur, prefix, m) + ('<div class="d98" id="d98-fichiers"><div class="d98-sousonglets" role="tablist" aria-label="Affichage des fichiers">%s</div>'
            '<div id="d98-fichiers-panneau" role="tabpanel" aria-labelledby="d98-sous-%s">%s</div></div>') % (onglets, sous, corps)


def _base(desk, acteur, m):
    """Répertoire du dossier : celui du registre (avec l'état du rangement), sinon celui de l'ancien registre JSON."""
    try:
        d = D.lire(desk, acteur, m['id'])
    except Stop as ex:
        if str(ex) != 'introuvable':
            raise
        return str(m.get('path') or ''), None
    return d['stockage']['chemin'] or '', d


def repertoire_html(desk, acteur, prefix, m, args):
    """« Par répertoire » : arborescence réelle du dossier, en lecture seule. 5.6.109 (F02, C02, C03, C09) : cinq colonnes triables (Nom,
    Type, Modifié le, Créé le, Taille) sur TOUT le répertoire avant la pagination — un bouton par en-tête (formulaire GET : Entrée et
    Espace, sans script), ``aria-sort`` sur la colonne triée ; un nouveau tri repart de la première page et garde dossier et répertoire ;
    les liens de pagination, du fil et des sous-répertoires portent le tri. Dates dans le fuseau du compte, sur dix caractères, avec
    l'instant complet en infobulle ; une métadonnée inconnue est écrite en mots (jamais « ctime » pour une création, jamais la somme des
    fichiers directs pour la taille d'un répertoire). Le chemin technique passe dans un détail. Avec la nouvelle interface
    (``interface56100``), le contenu des sous-répertoires est compté à la demande (``v56109-repertoire.js``) ; l'ancienne présentation
    garde le comptage à l'ouverture."""
    from .fiche460 import doc_href
    from . import tri56109 as T
    mid = m['id']
    base, d = _base(desk, acteur, m)
    if d is not None and d['stockage']['etat'] != 'pret':
        return ('<div class="d97-rangement" role="status"><p><strong>%s.</strong> %s</p><p>La liste des fichiers s’affichera quand le répertoire sera prêt.</p>'
                '<form class="d97-form" data-d97-api="%s" data-d97-apres="recharger"><input type="hidden" name="cle" value="%s">'
                '<button class="ax-btn ghost" type="submit">Reprendre le rangement</button><span class="d97-statut" role="status"></span></form></div>') % (
            e(d['stockage']['libelle'] or 'Répertoire pas encore prêt'), e(d['stockage']['motif'] or 'Aucun répertoire n’est encore rattaché à ce dossier.'),
            _api(d['id'], 'rangement'), _cle())
    page = int(args.get('page')) if str(args.get('page') or '').isdigit() and int(args.get('page')) > 0 else 1
    tri, sens = T.reglage(args.get('tri'), args.get('sens'))
    garde = {'tri': tri, 'sens': sens} if (tri, sens) != T.DEFAUT else {}        # tri par défaut : adresses inchangées
    try:
        from .interface56100 import active
        paresseux = d is not None and active(desk, acteur[0])
    except Exception:   # noqa: BLE001 — réglage illisible : comptage à l'ouverture, comme avant
        paresseux = False
    try:
        from .acces5683 import today_of
        fuseau = today_of(desk, acteur[0])[1]
    except Exception:   # noqa: BLE001 — fuseau illisible : celui du cabinet par défaut
        fuseau = None
    t = F.arborescence(desk, base, str(args.get('rep') or ''), page, tri=tri, sens=sens, comptes=not paresseux, fuseau=fuseau)
    if t['etat'] == 'sans_repertoire':
        lien = ('<form class="d97-form" data-d97-api="%s" data-d97-apres="recharger"><input type="hidden" name="cle" value="%s">'
                '<button class="ax-btn ghost" type="submit">Reprendre le rangement</button><span class="d97-statut" role="status"></span></form>' % (
                    _api(d['id'], 'rangement'), _cle())) if d is not None else ''
        return '<div class="d97-rangement" role="status"><p><strong>Aucun répertoire rattaché.</strong> %s</p>%s<p><a href="%s">Rattacher un répertoire existant</a></p></div>' % (
            e(t['motif']), lien, e(prefix + '/dossiers/nouveau?emplacement=rattacher', quote=True))
    fil = '<a href="%s">%s</a>' % (_q(prefix, mid, 'fichiers', sous='repertoire', **garde), e('Répertoire du dossier'))
    for x in t['fil']:
        fil += ' › <a href="%s">%s</a>' % (_q(prefix, mid, 'fichiers', sous='repertoire', rep=x['rel'], **garde), e(x['nom']))
    entete = '<nav class="d98-fil" aria-label="Emplacement">%s</nav>' % fil
    if t['base']:   # 5.6.109 (C03) : le chemin technique reste accessible, dans un détail, sans occuper la première zone utile
        entete += '<details class="d98-chemin"><summary>Chemin complet dans le stockage</summary><p class="ax-muted">%s</p></details>' % e(t['chemin'] or t['base'])
    if t['etat'] != 'ok':
        return entete + '<p class="d98-etat d98-etat-%s" role="status"><strong>%s</strong></p>' % (e(t['etat'], quote=True), e(t['motif']))

    ORDRES = {'nom': ('de A à Z', 'de Z à A'), 'type': ('de A à Z', 'de Z à A'), 'modifie': ('du plus ancien au plus récent', 'du plus récent au plus ancien'),
              'cree': ('du plus ancien au plus récent', 'du plus récent au plus ancien'), 'taille': ('du plus petit au plus gros', 'du plus gros au plus petit')}
    ordre = lambda champ, s: ORDRES[champ][0 if s == 'asc' else 1]

    def colonne(champ):
        actif = champ == tri
        prochain = T.suivant(champ, tri, sens)
        caches = ''.join('<input type="hidden" name="%s" value="%s">' % (k, e(str(v), quote=True)) for k, v in (
            ('id', mid), ('vue', 'fichiers'), ('sous', 'repertoire'), ('rep', t['rel']), ('tri', champ), ('sens', prochain)) if v != '' or k == 'rep')
        etat = (' : trié %s ; activer pour trier %s' % (ordre(champ, sens), ordre(champ, prochain))) if actif else (' : trier %s' % ordre(champ, prochain))
        fleche = ('<span class="r109-fleche" aria-hidden="true">%s</span>' % ('▲' if sens == 'asc' else '▼')) if actif else ''
        return ('<th scope="col"%s><form method="get" action="%s" class="r109-tri">%s<button type="submit" class="r109-tri-bouton" title="Trier par %s, %s">'
                '%s%s<span class="sr-only">%s</span></button></form></th>') % (
            (' aria-sort="%s"' % ('ascending' if sens == 'asc' else 'descending')) if actif else '', e(prefix + '/dossier', quote=True), caches,
            e(T.LIBELLES[champ].lower(), quote=True), e(ordre(champ, prochain), quote=True), e(T.LIBELLES[champ]), fleche, e(etat))

    def inconnu(mot, raison):
        return '<span class="r109-inconnu" title="%s">%s</span>' % (e(raison, quote=True), e(mot))

    def cellule_date(x, champ):
        valeur, source = x.get(champ) or '', x.get(champ + '_source') or ''
        if not valeur:
            if champ == 'cree':
                return inconnu('Non disponible', 'Le stockage ne fournit pas la date de création de cet élément.')
            return inconnu('Inconnue', 'Le stockage ne fournit pas de date de modification lisible pour cet élément.')
        complet = T.horodatage_fr(source, fuseau) if source else ''
        return '<time datetime="%s"%s>%s</time>' % (e(valeur, quote=True), (' title="%s"' % e(complet, quote=True)) if complet else '', e(_date_fr(valeur)))

    lignes = []
    for x in t['dossiers']:
        if x['fichiers'] is not None:
            compte = '%d fichier%s' % (x['fichiers'], '' if x['fichiers'] == 1 else 's')
            if x['fichiers']:
                compte += ' (%s)' % F.taille_lisible(x['taille'])
            if x['sous_dossiers']:
                compte += ', %d sous-répertoire%s' % (x['sous_dossiers'], '' if x['sous_dossiers'] == 1 else 's')
            if x.get('incomplet'):
                compte += ' — compte incomplet'
            contenu = ' · ' + e(compte)
        elif x.get('illisible'):
            contenu = ' · lecture impossible'
        elif paresseux and x['ouvrable']:
            contenu = ' · <span class="r109-compte" data-r109-compter="%s">contenu non compté</span>' % e(x['rel'], quote=True)
        else:
            contenu = ' · contenu non compté'
        nom = ('<a href="%s">%s</a>' % (_q(prefix, mid, 'fichiers', sous='repertoire', rep=x['rel'], **garde), e(x['nom']))) if x['ouvrable'] else e(x['nom'])
        lignes.append('<tr class="d98-rep"><th scope="row" data-label="Nom"><span class="d98-icone" aria-hidden="true">▸</span> %s</th><td data-label="Type">Répertoire%s</td>'
                      '<td data-label="Modifié le" class="r109-date">%s</td><td data-label="Créé le" class="r109-date">%s</td><td data-label="Taille" class="r109-taille">%s</td></tr>' % (
                          nom, contenu, cellule_date(x, 'modifie'), cellule_date(x, 'cree'),
                          inconnu('Non calculée', 'La taille totale d’un répertoire n’est pas calculée : ses sous-répertoires ne sont pas parcourus.')))
    for x in t['fichiers']:
        genre = x['type'].upper() if x.get('type') else 'Sans extension'
        taille = F.taille_lisible(x['taille']) if x['taille'] is not None else ''
        resume = ' · '.join(v for v in (genre, taille, ('modifié le ' + _date_fr(x['modifie'])) if x['modifie'] else '') if v)
        lignes.append('<tr class="d98-fic"><th scope="row" data-label="Nom"><a class="d79-doc" href="%s" data-d79-doc="%s" data-r109-resume="%s">%s</a></th>'
                      '<td data-label="Type">%s</td><td data-label="Modifié le" class="r109-date">%s</td><td data-label="Créé le" class="r109-date">%s</td>'
                      '<td data-label="Taille" class="r109-taille">%s</td></tr>' % (
                          e(prefix + doc_href(x['chemin'], mid), quote=True), e(x['chemin'], quote=True), e(resume, quote=True), e(x['nom']), e(genre),
                          cellule_date(x, 'modifie'), cellule_date(x, 'cree'),
                          e(taille) if taille else inconnu('Inconnue', 'Le stockage ne donne pas la taille de ce fichier.')))
    if not lignes:
        tableau = '<p class="ax-muted" role="status">Ce répertoire est vide.</p>'
    else:
        etat = '%d élément%s · tri : %s, %s' % (t['total'], '' if t['total'] == 1 else 's', T.LIBELLES[tri].lower(), ordre(tri, sens))
        tableau = ('<p class="ax-muted r109-etat" role="status">%s</p><div class="d97-tableau"%s><table class="d97-table d98-arbre"><caption class="sr-only">Contenu du '
                   'répertoire, trié par %s %s</caption><thead><tr>%s</tr></thead><tbody>%s</tbody></table></div>') % (
            e(etat), (' data-r109-api="%s"' % _api(d['id'], 'fichiers')) if paresseux else '', e(T.LIBELLES[tri].lower()), e(ordre(tri, sens)),
            ''.join(colonne(c) for c in ('nom', 'type', 'modifie', 'cree', 'taille')), ''.join(lignes))
    notes = []
    if t['partiel']:
        notes.append('Tri partiel : ce répertoire contient %d entrées ; seules les %d premières lues sont triées et affichées. Ouvrez-le dans Nextcloud pour la suite.' % (
            t['presents'], t['total']))
    if t['non_comptes']:
        notes.append('%d sous-répertoire(s) non comptés sur cette page (lecture limitée).' % t['non_comptes'])
    if lignes:
        notes.append('« Créé le » est la date de création du fichier dans le stockage, quand il la fournit ; ce n’est pas la date de rédaction de l’acte.'
                     + (' Sur ce stockage local, elle n’est connue que si le système de fichiers l’enregistre.' if t['cree_origine'] == 'naissance' else ''))
    pager = ''
    if t['pages'] > 1:
        nav = []
        if t['page'] > 1:
            nav.append('<a href="%s">← Précédents</a>' % _q(prefix, mid, 'fichiers', sous='repertoire', rep=t['rel'], **garde, page=t['page'] - 1))
        nav.append('<span>Page %d / %d (%d éléments)</span>' % (t['page'], t['pages'], t['total']))
        if t['page'] < t['pages']:
            nav.append('<a href="%s">Voir la suite →</a>' % _q(prefix, mid, 'fichiers', sous='repertoire', rep=t['rel'], **garde, page=t['page'] + 1))
        pager = '<nav class="d51-pager" aria-label="Pages du répertoire">%s</nav>' % ' '.join(nav)
    return entete + tableau + pager + ''.join('<p class="ax-muted">%s</p>' % e(n) for n in notes) + (
        '<p class="ax-muted">Lecture seule. Un clic sur un fichier ouvre l’aperçu ; pour déplacer ou renommer, utilisez Nextcloud.</p>')
