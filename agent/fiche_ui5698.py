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
    out = []
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
    roles = options_roles(p.get('role') or premier_role.get(groupe, 'adverse'), (groupe,) if groupe else None)
    choix_avocat = '<option value="">Aucun / non désigné</option>' + ''.join(
        '<option value="%s"%s>%s (%s)</option>' % (e(a['id'], quote=True), ' selected' if a['id'] == p.get('avocat_id') else '', e(a['nom']), e(a['role_libelle']))
        for a in avocats if a['id'] != p.get('id'))
    return ('<div class="d98-grille">'
            + _champ('Nom ou dénomination', 'nom', p.get('nom'), 160)
            + '<label class="d98-champ">Rôle<select name="role">%s</select>%s</label>' % (roles, _erreur('role'))
            + _champ('Qualité', 'qualite', p.get('qualite'), 160, aide='Ex. demandeur, intimée, gérant, expert judiciaire')
            + '<label class="d98-champ">Personne<select name="type_personne">%s</select>%s</label>' % (_options(F.TYPES_PERSONNE, p.get('type_personne', '')), _erreur('type_personne'))
            + _champ('Courriel', 'courriel', p.get('courriel'), 254, 'email')
            + _champ('Téléphone', 'telephone', p.get('telephone'), 30, 'tel')
            + _champ('Adresse', 'adresse', p.get('adresse'), 300, large=True)
            + _champ('SIREN (société)', 'siren', p.get('siren'), 11, aide='Facultatif, personne morale : 9 chiffres')
            + ('<label class="d98-champ">Représenté(e) par<select name="avocat_id">%s</select>%s</label>' % (choix_avocat, _erreur('avocat_id')))
            + '<label class="d98-champ d98-large">Note<textarea name="note" rows="2" maxlength="1000">%s</textarea>%s</label>' % (e(p.get('note') or ''), _erreur('note'))
            + '</div>')


def _ligne_partie(prefix, did, p, avocats):
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
            '<p class="d98-details">%s</p>%s%s%s%s%s<div class="d98-gestes">%s%s</div></li>') % (
        e(p['nom']), e(p['role_libelle']), ' · '.join(details) or '<span class="ax-muted">Qualité à préciser</span>',
        ('<p class="d98-contact">%s</p>' % contact) if contact else '', adresse, avocat, conflit, note, modifier, retirer)


AJOUTER = {'clients': 'Ajouter un client', 'adverses': 'Ajouter une partie adverse', 'avocats': 'Ajouter un avocat', 'autres': 'Ajouter un intervenant'}
VIDES = {'clients': 'Aucun client inscrit.', 'adverses': 'Aucune partie adverse inscrite.', 'avocats': 'Aucun avocat inscrit (du client, adverse ou postulant).',
         'autres': 'Aucun autre intervenant (expert, commissaire de justice, mandataire, notaire, assureur…).'}


def parties_html(desk, acteur, prefix, m):
    try:
        data = F.parties(desk, acteur, m['id'])
    except Stop as ex:
        if str(ex) != 'introuvable':
            raise
        return _hors_registre(prefix, acteur) + _proposees_lecture(desk, m)
    did = data['dossier']
    avocats = [p for p in data['parties'] if p['role'] in F.ROLES_AVOCATS]
    out = ['<div class="d98" id="d98-parties"><p class="d98-intro ax-muted">Les parties inscrites ici sont celles du dossier ; elles servent aux courriers, '
           'aux conflits d’intérêts et à la recherche. Ajouter une partie adverse relance la vérification des conflits.</p>']
    for cle, libelle, liste in data['groupes']:
        lignes = ''.join(_ligne_partie(prefix, did, p, avocats) for p in liste) or '<li class="ax-muted d98-vide">%s</li>' % e(VIDES[cle])
        ajout = '<details class="d98-ajout"><summary class="ax-btn ghost">%s</summary>%s</details>' % (
            e(AJOUTER[cle]), _formulaire(_api(did, 'parties'), _corps_partie(None, avocats, cle), 'Ajouter'))
        out.append('<section class="d98-groupe d79-card" aria-labelledby="d98-g-%s"><div class="d79-card-head"><h2 id="d98-g-%s">%s <span class="d98-compte">%d</span></h2></div>'
                   '<ul class="d98-liste">%s</ul>%s</section>' % (cle, cle, e(libelle), len(liste), lignes, ajout))
    try:
        proposees = F.parties_proposees(desk, did, data['parties'])
    except Exception:   # noqa: BLE001 — lecture des pièces indisponible : les parties inscrites restent affichées
        proposees = None
    out.append(_proposees_html(did, proposees))
    out.append('</div>')
    return ''.join(out)


def _proposees_html(did, proposees):
    if proposees is None:
        corps = '<p class="ax-muted">Lecture des parties citées dans les pièces indisponible pour le moment.</p>'
    elif not proposees:
        corps = '<p class="ax-muted">Aucune autre partie lue dans les pièces. « ↻ Actualiser » relit les en-têtes des actes.</p>'
    else:
        lignes = []
        for p in proposees:
            info = ' · '.join(e(x) for x in (p['qualite'], ('conseil : ' + p['conseil']) if p['conseil'] else '') if x)
            form = _formulaire(_api(did, 'parties'), '<input type="hidden" name="nom" value="%s"><input type="hidden" name="qualite" value="%s">'
                               '<label class="d98-champ d98-inline">Rôle<select name="role">%s</select></label>%s%s' % (
                                   e(p['nom'], quote=True), e(p['qualite'], quote=True), options_roles(p['role']), _erreur('role'), _erreur('nom')),
                               'Ajouter au dossier', 'd98-form-ligne')
            lignes.append('<li class="d98-propose"><strong>%s</strong>%s%s</li>' % (e(p['nom']), (' <span class="ax-muted">— %s</span>' % info) if info else '', form))
        corps = '<ul class="d98-liste">%s</ul>' % ''.join(lignes)
    return ('<section class="d98-groupe d98-proposees d79-card" aria-labelledby="d98-g-proposees"><div class="d79-card-head"><h2 id="d98-g-proposees">'
            'Proposées, à valider</h2></div><p class="ax-muted">Parties lues dans les pièces du dossier. Elles ne sont jamais ajoutées automatiquement : '
            'vérifiez le rôle puis « Ajouter au dossier ».</p>%s</section>') % corps


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
    return ('<div class="d98" id="d98-fichiers"><div class="d98-sousonglets" role="tablist" aria-label="Affichage des fichiers">%s</div>'
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
    from .fiche460 import doc_href
    mid = m['id']
    base, d = _base(desk, acteur, m)
    if d is not None and d['stockage']['etat'] != 'pret':
        return ('<div class="d97-rangement" role="status"><p><strong>%s.</strong> %s</p><p>La liste des fichiers s’affichera quand le répertoire sera prêt.</p>'
                '<form class="d97-form" data-d97-api="%s" data-d97-apres="recharger"><input type="hidden" name="cle" value="%s">'
                '<button class="ax-btn ghost" type="submit">Reprendre le rangement</button><span class="d97-statut" role="status"></span></form></div>') % (
            e(d['stockage']['libelle'] or 'Répertoire pas encore prêt'), e(d['stockage']['motif'] or 'Aucun répertoire n’est encore rattaché à ce dossier.'),
            _api(d['id'], 'rangement'), _cle())
    page = int(args.get('page')) if str(args.get('page') or '').isdigit() and int(args.get('page')) > 0 else 1
    t = F.arborescence(desk, base, str(args.get('rep') or ''), page)
    if t['etat'] == 'sans_repertoire':
        lien = ('<form class="d97-form" data-d97-api="%s" data-d97-apres="recharger"><input type="hidden" name="cle" value="%s">'
                '<button class="ax-btn ghost" type="submit">Reprendre le rangement</button><span class="d97-statut" role="status"></span></form>' % (
                    _api(d['id'], 'rangement'), _cle())) if d is not None else ''
        return '<div class="d97-rangement" role="status"><p><strong>Aucun répertoire rattaché.</strong> %s</p>%s<p><a href="%s">Rattacher un répertoire existant</a></p></div>' % (
            e(t['motif']), lien, e(prefix + '/dossiers/nouveau?emplacement=rattacher', quote=True))
    fil = '<a href="%s">%s</a>' % (_q(prefix, mid, 'fichiers', sous='repertoire'), e('Répertoire du dossier'))
    for x in t['fil']:
        fil += ' › <a href="%s">%s</a>' % (_q(prefix, mid, 'fichiers', sous='repertoire', rep=x['rel']), e(x['nom']))
    entete = '<nav class="d98-fil" aria-label="Emplacement">%s</nav>' % fil
    if t['base']:
        entete += '<p class="ax-muted d98-chemin">%s</p>' % e(t['chemin'] or t['base'])
    if t['etat'] != 'ok':
        return entete + '<p class="d98-etat d98-etat-%s" role="status"><strong>%s</strong></p>' % (e(t['etat'], quote=True), e(t['motif']))
    lignes = []
    for x in t['dossiers']:
        compte = ('%d fichier%s' % (x['fichiers'], '' if x['fichiers'] == 1 else 's') + ((', %d sous-répertoire%s' % (x['sous_dossiers'], '' if x['sous_dossiers'] == 1 else 's'))
                                                                                        if x['sous_dossiers'] else '')) if x['fichiers'] is not None else (
            'lecture impossible' if x.get('illisible') else 'non compté')
        nom = ('<a href="%s">%s</a>' % (_q(prefix, mid, 'fichiers', sous='repertoire', rep=x['rel']), e(x['nom']))) if x['ouvrable'] else e(x['nom'])
        lignes.append('<tr class="d98-rep"><th scope="row" data-label="Nom"><span class="d98-icone" aria-hidden="true">▸</span> %s</th><td data-label="Contenu">%s</td>'
                      '<td data-label="Modifié le">%s</td><td data-label="Taille">%s</td></tr>' % (
                          nom, e(compte), e(_date_fr(x['modifie']) or '—'), e(F.taille_lisible(x['taille']) or '—')))
    for x in t['fichiers']:
        lignes.append('<tr class="d98-fic"><th scope="row" data-label="Nom"><a class="d79-doc" href="%s" data-d79-doc="%s">%s</a></th><td data-label="Contenu">Fichier</td>'
                      '<td data-label="Modifié le">%s</td><td data-label="Taille">%s</td></tr>' % (
                          e(prefix + doc_href(x['chemin'], mid), quote=True), e(x['chemin'], quote=True), e(x['nom']), e(_date_fr(x['modifie']) or '—'),
                          e(F.taille_lisible(x['taille']))))
    if not lignes:
        tableau = '<p class="ax-muted" role="status">Ce répertoire est vide.</p>'
    else:
        tableau = ('<div class="d97-tableau"><table class="d97-table d98-arbre"><caption class="sr-only">Contenu du répertoire</caption><thead><tr><th scope="col">Nom</th>'
                   '<th scope="col">Contenu</th><th scope="col">Modifié le</th><th scope="col">Taille</th></tr></thead><tbody>%s</tbody></table></div>') % ''.join(lignes)
    notes = []
    if t['tronquee']:
        notes.append('Liste tronquée aux %d premières entrées : ouvrez le répertoire dans Nextcloud pour la suite.' % F.MAX_LUES)
    if t['non_comptes']:
        notes.append('%d sous-répertoire(s) non comptés sur cette page (lecture limitée).' % t['non_comptes'])
    pager = ''
    if t['pages'] > 1:
        nav = []
        if t['page'] > 1:
            nav.append('<a href="%s">← Précédents</a>' % _q(prefix, mid, 'fichiers', sous='repertoire', rep=t['rel'], page=t['page'] - 1))
        nav.append('<span>Page %d / %d (%d éléments)</span>' % (t['page'], t['pages'], t['total']))
        if t['page'] < t['pages']:
            nav.append('<a href="%s">Voir la suite →</a>' % _q(prefix, mid, 'fichiers', sous='repertoire', rep=t['rel'], page=t['page'] + 1))
        pager = '<nav class="d51-pager" aria-label="Pages du répertoire">%s</nav>' % ' '.join(nav)
    return entete + tableau + pager + ''.join('<p class="ax-muted">%s</p>' % e(n) for n in notes) + (
        '<p class="ax-muted">Lecture seule. Un clic sur un fichier ouvre l’aperçu ; pour déplacer ou renommer, utilisez Nextcloud.</p>')
