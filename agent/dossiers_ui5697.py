"""5.6.97 (lot dossiers, § 9 à § 12) : écrans Dossiers et Nouveau dossier, onglets de la fiche, classement d'un courriel à la main.

HTML rendu par le serveur, échappé par ``html.escape`` ; les gestes passent par ``v5697-dossiers.js`` (aucun script en ligne) vers l'API
``m57/dossiers``. Les droits sont appliqués par le registre (``dossiers5697.lister`` / ``lire`` → ``acces5697``) AVANT recherche, tri et
compteurs : un dossier réservé n'apparaît ni dans une ligne, ni dans un compteur, ni dans une liste de responsables. Aucun appel à un modèle.
"""
from datetime import date, datetime, timezone
from html import escape as e
from urllib.parse import urlencode
import uuid

from .common import Stop
from . import dossiers5697 as D

FILTRES_STATUT = (('actif', 'Actifs'), ('archive', 'Archivés'))
FILTRES_NATURE = (('', 'Toutes natures'), ('conseil', 'Conseil'), ('contentieux', 'Contentieux'), ('a_qualifier', 'À qualifier'))
ONGLETS = (('synthese', 'Synthèse'), ('parties', 'Parties'), ('juridiction', 'Juridiction'), ('fichiers', 'Fichiers'), ('correspondances', 'Correspondances'),
           ('procedure', 'Procédure · Pièces'), ('agenda', 'Agenda et tâches'), ('gestion', 'Gestion'))   # 5.6.98 : documentation (dossier5679.TABS fait foi)


def _date_fr(valeur):
    valeur = str(valeur or '')[:10]
    try:
        return date.fromisoformat(valeur).strftime('%d/%m/%Y')
    except ValueError:
        return ''


def _lignes_sql(desk, sql, params=()):
    try:
        return desk.db.execute(sql, params).fetchall()
    except Exception:   # noqa: BLE001 — table d'un module absent : aucune donnée, jamais une erreur de page
        return []


def prochaines_dates(desk, ids):
    """{dossier: (date, libellé)} : prochaine date CONFIRMÉE (échéance confirmée ou saisie, rendez-vous de l'agenda) ; et
    {dossier: nombre} des dates proposées, « À vérifier », comptées à part."""
    ids = list(ids)
    if not ids:
        return {}, {}
    jour = date.today().isoformat()
    place = ','.join('?' * len(ids))
    confirmees, a_verifier = {}, {}
    for r in _lignes_sql(desk, "SELECT matter,due,rule_id,note,status FROM deadlines450 WHERE matter IN (%s) AND due>=? AND due<>'' "
                               "AND status IN ('a_confirmer','confirmee','manuel') ORDER BY due" % place, (*ids, jour)):
        if r['status'] == 'a_confirmer':
            a_verifier[r['matter']] = a_verifier.get(r['matter'], 0) + 1
        elif r['matter'] not in confirmees:
            confirmees[r['matter']] = (r['due'][:10], 'Échéance — ' + (r['note'] or str(r['rule_id']).replace('_', ' '))[:120])
    maintenant = datetime.now(timezone.utc).isoformat()
    for r in _lignes_sql(desk, 'SELECT matter,title,starts FROM calendar_cache WHERE matter IN (%s) AND starts>=? ORDER BY starts' % place, (*ids, maintenant)):
        jour_ev = str(r['starts'])[:10]
        if r['matter'] not in confirmees or jour_ev < confirmees[r['matter']][0]:
            confirmees[r['matter']] = (jour_ev, str(r['title'] or 'Rendez-vous')[:120])
    return confirmees, a_verifier


def dernieres_activites(desk, ids):
    ids = list(ids)
    if not ids:
        return {}
    place = ','.join('?' * len(ids))
    out = {r['matter']: r['last_external_activity'] or '' for r in _lignes_sql(
        desk, 'SELECT matter,last_external_activity FROM matter_portfolio WHERE matter IN (%s)' % place, ids)}
    for r in _lignes_sql(desk, 'SELECT dossier_id, MAX(quand) AS q FROM dossier_journal5697 WHERE dossier_id IN (%s) GROUP BY dossier_id' % place, ids):
        if (r['q'] or '') > out.get(r['dossier_id'], ''):
            out[r['dossier_id']] = r['q']
    return out


def portefeuille(desk, acteur, ids):
    """Prochaine action, date utile, état et dernière activité calculés par le lot documents-mission (``portefeuille5697``, § 17) ;
    module indisponible : None (affiché « indisponible », jamais un faux « — »)."""
    try:
        from .portefeuille5697 import lignes_portefeuille
        return lignes_portefeuille(desk, acteur, list(ids))
    except Exception:   # noqa: BLE001
        return None


def _action_html(suivi, ident):
    if suivi is None:
        return 'indisponible'
    ligne = suivi.get(ident) or {}
    action = ligne.get('action')
    if action:
        return e(action.get('libelle') or action.get('type_libelle') or '')
    sugg = ligne.get('suggestion')
    if sugg:
        return '<span class="d97-suggestion">Suggestion : %s</span>' % e(sugg.get('libelle') or '')
    return '<span class="ax-muted">Aucune prochaine action</span>'


def _membres(desk, auth):
    try:
        from .membres5650 import directory
        return {k: v.get('name') or k for k, v in directory(desk, auth).items()}
    except Exception:   # noqa: BLE001
        return {}


def _lien(prefix, chemin, **params):
    return e(prefix + chemin + ('?' + urlencode({k: v for k, v in params.items() if v not in (None, '', False)}) if params else ''), quote=True)


def _cle():
    return uuid.uuid4().hex


# ------------------------------------------------------------------------------------------------ § 9 : écran Dossiers
def ecran(desk, env, auth, prefix, args):
    """Écran de départ : en-tête (titre, recherche, + Nouveau dossier), filtres, tableau, état vide, menu secondaire par ligne."""
    from .web567 import actor
    acteur = actor(env)
    q = str(args.get('q') or '')[:120]
    mes = str(args.get('mes') or '') == '1'
    statut = args.get('statut') if args.get('statut') in dict(FILTRES_STATUT) else 'actif'
    nature = args.get('nature') if args.get('nature') in dict(FILTRES_NATURE) else ''
    responsable = str(args.get('responsable') or '')[:120]
    verifier = str(args.get('verifier') or '') == '1'
    tous = D.lister(desk, acteur, {'statut': statut}, 5000)          # périmètre de l'acteur : base des compteurs et de la liste des responsables
    lignes = [d for d in D.lister(desk, acteur, {'q': q, 'mes': mes, 'statut': statut, 'nature': nature, 'responsable': responsable}, 5000)]
    ids = [d['id'] for d in lignes]
    confirmees, proposees = prochaines_dates(desk, ids)
    activites = dernieres_activites(desk, ids)
    suivi = portefeuille(desk, acteur, ids)
    if verifier:
        lignes = [d for d in lignes if proposees.get(d['id'])]
    membres = _membres(desk, auth)
    nb_verifier = sum(proposees.values())
    base = {'q': q, 'mes': '1' if mes else '', 'statut': statut, 'nature': nature, 'responsable': responsable}

    def bascule(libelle, **changes):
        params = {**base, **changes}
        actif = all(base.get(k) == v for k, v in changes.items())
        return '<a class="d97-filtre" href="%s"%s>%s</a>' % (_lien(prefix, '/dossiers', **params), ' aria-current="true"' if actif else '', e(libelle))

    responsables = sorted({d['responsable'] for d in tous if d.get('responsable')})
    options_resp = '<option value="">Tous les responsables</option>' + ''.join(
        '<option value="%s"%s>%s</option>' % (e(r, quote=True), ' selected' if r == responsable else '', e(membres.get(r, r))) for r in responsables)
    out = ['<section id="d97" class="d97" aria-labelledby="d97-titre" data-d97-liste="1">',
           '<header class="d97-tete"><h2 id="d97-titre" class="sr-only">Liste des dossiers</h2>'
           '<form class="d97-recherche" method="get" role="search">%s<label class="sr-only" for="d97-q">Rechercher par client, référence ou partie</label>'
           '<input id="d97-q" name="q" value="%s" placeholder="Client, référence ou partie" data-no-dictation><button class="ax-btn ghost">Rechercher</button></form>'
           '<a class="ax-btn d97-nouveau" href="%s">+ Nouveau dossier</a></header>' % (
               ''.join('<input type="hidden" name="%s" value="%s">' % (k, e(v, quote=True)) for k, v in base.items() if v and k != 'q'),
               e(q, quote=True), _lien(prefix, '/dossiers/nouveau'))]
    out.append('<nav class="d97-filtres" aria-label="Filtres des dossiers"><div role="group" aria-label="Portée">%s%s</div>'
               '<div role="group" aria-label="État">%s</div><div role="group" aria-label="Nature">%s</div>'
               '<form method="get" class="d97-resp">%s<label for="d97-resp">Responsable</label><select id="d97-resp" name="responsable" data-d97-auto>%s</select>'
               '<button class="ax-btn ghost d97-sans-js">Filtrer</button></form></nav>' % (
                   bascule('Mes dossiers', mes='1'), bascule('Tous ceux auxquels j’ai accès', mes=''),
                   ''.join(bascule(t, statut=k) for k, t in FILTRES_STATUT), ''.join(bascule(t, nature=k) for k, t in FILTRES_NATURE),
                   ''.join('<input type="hidden" name="%s" value="%s">' % (k, e(v, quote=True)) for k, v in base.items() if v and k != 'responsable'),
                   options_resp))
    out.append('<p class="d97-compteurs" role="status">%d dossier%s%s · <a href="%s"%s>%d date%s proposée%s à vérifier</a></p>' % (
        len(lignes), '' if len(lignes) == 1 else 's', ' affiché' + ('' if len(lignes) == 1 else 's') if (q or mes or nature or responsable or verifier) else '',
        _lien(prefix, '/dossiers', **{**base, 'verifier': '' if verifier else '1'}), ' aria-current="true"' if verifier else '',
        nb_verifier, '' if nb_verifier == 1 else 's', '' if nb_verifier == 1 else 's'))
    if not D.migration_faite(desk) and any(d.get('provenance') == 'non_importe' for d in lignes) and acteur[1] == 'administrateur':
        out.append('<div class="d97-avis" role="note"><p>Les dossiers existants sont affichés en lecture ; importez-les dans le registre unique '
                   'pour les modifier ici. <a href="%s">Préparer l’import (simulation d’abord)</a></p></div>' % _lien(prefix, '/dossiers/import'))
    if not lignes:
        vide = 'Aucun dossier ne correspond à ces filtres.' if (q or mes or nature or responsable or verifier or statut != 'actif') else 'Aucun dossier pour le moment.'
        out.append('<div class="d97-vide"><p>%s</p><p><a class="ax-btn" href="%s">Créer un dossier</a> <a class="ax-btn ghost" href="%s">Rattacher un dossier existant</a></p></div>' % (
            e(vide), _lien(prefix, '/dossiers/nouveau'), _lien(prefix, '/dossiers/nouveau', emplacement='rattacher')))
    else:
        corps = []
        for d in lignes:
            date_c = confirmees.get(d['id'])
            prop = proposees.get(d['id'], 0)
            prochaine = ('%s — %s' % (_date_fr(date_c[0]), date_c[1])) if date_c else '—'
            if prop:
                prochaine += ' <span class="d97-a-verifier">%d à vérifier</span>' % prop
            menu = ['<a href="%s">Classer un courriel</a>' % _lien(prefix, '/courriels', dossier=d['id']),
                    '<a href="%s">Ajouter une tâche</a>' % _lien(prefix, '/mes-taches', matter=d['id'])]
            if d.get('version') and acteur[1] in ('administrateur', 'avocat'):
                cible = 'actif' if d['statut'] == 'archive' else 'archive'
                menu.append('<form class="d97-form" data-d97-api="m57/dossiers/%s/modifier" data-d97-apres="recharger">'
                            '<input type="hidden" name="cle" value="%s"><input type="hidden" name="version" value="%d"><input type="hidden" name="statut" value="%s">'
                            '<button type="submit" class="d97-lien">%s</button><span class="d97-statut" role="status"></span></form>' % (
                                e(d['id'], quote=True), _cle(), d['version'], cible, 'Réactiver' if cible == 'actif' else 'Archiver'))
            corps.append(
                '<tr data-d97-ligne="%s"><th scope="row" data-label="Dossier"><a class="d97-ouvrir" href="%s">%s</a><span class="d97-client">%s</span>'
                '<span class="d97-ref">%s</span><button type="button" class="d97-plus" data-d97-details aria-expanded="false">Détails</button></th><td data-label="Type">%s</td><td data-label="Responsable">%s</td>'
                '<td data-label="Prochaine action" class="d97-action">%s</td><td data-label="Date utile">%s</td>'
                '<td data-label="Prochaine date confirmée" class="d97-prochaine">%s</td><td data-label="État">%s</td><td data-label="Dernière activité">%s</td>'
                '<td class="d97-menu-cell"><details class="d97-menu"><summary aria-label="Autres actions pour %s">…</summary><div class="d97-menu-corps">%s</div></details></td></tr>' % (
                    e(d['id'], quote=True), _lien(prefix, '/dossier', id=d['id']), e(d['titre']),
                    e(('Client : ' + d['client']) if d.get('client') else 'Client à identifier'), e(d['reference']),
                    e(d['nature_libelle']), e(membres.get(d.get('responsable'), d.get('responsable') or '—')),
                    _action_html(suivi, d['id']), e(_date_fr((suivi or {}).get(d['id'], {}).get('date_utile')) or '—'), prochaine,
                    e(((suivi or {}).get(d['id']) or {}).get('etat_libelle') or ('indisponible' if suivi is None else '—')),
                    e(_date_fr(max(activites.get(d['id'], d.get('modifie_le')) or '', ((suivi or {}).get(d['id']) or {}).get('derniere_activite') or '')) or '—'), e(d['titre'], quote=True), ''.join(menu)))
        out.append('<div class="d97-tableau"><table class="d97-table"><caption class="sr-only">Dossiers</caption><thead><tr>'
                   '<th scope="col">Dossier et client</th><th scope="col">Type</th><th scope="col">Responsable</th>'
                   '<th scope="col">Prochaine action</th><th scope="col">Date utile</th><th scope="col">Prochaine date confirmée</th><th scope="col">État</th><th scope="col">Dernière activité</th><th scope="col"><span class="sr-only">Actions</span></th>'
                   '</tr></thead><tbody>%s</tbody></table></div>' % ''.join(corps))
    out.append('</section>')
    return ''.join(out)


# ------------------------------------------------------------------------------------------------ § 10 : écran Nouveau dossier
def _message_source(desk, args):
    """Courriel d'origine (« Créer un dossier depuis ce message ») : en-têtes relus dans la boîte (lecture seule), jamais d'après l'adresse."""
    uid, boite = str(args.get('uid') or ''), str(args.get('boite') or '')
    if not uid.isdigit() or not boite:
        return None
    try:
        from .boite5625 import message
        m = message(desk, uid, folder=boite)
    except Stop:
        return None
    from email.utils import parseaddr
    nom, adresse = parseaddr(m.get('from') or '')
    return {'uid': m['uid'], 'boite': m['folder'], 'uidvalidity': str(m.get('uidvalidity') or args.get('uidvalidity') or ''),
            'message_id': m.get('message_id') or '', 'sujet': m.get('subject') or '', 'expediteur_nom': nom, 'expediteur': adresse, 'date': m.get('date') or ''}


def page_nouveau(desk, env, auth, prefix, args):
    from .web567 import actor
    compte, role = actor(env)
    membres = _membres(desk, auth)
    reference = D.proposer_reference(desk)
    msg = _message_source(desk, args) if args.get('depuis') == 'courriel' else None
    titre = (msg or {}).get('sujet', '')[:160]
    try:
        racine = D.racine(desk)
    except Stop:
        racine = ''
    resp = ''.join('<option value="%s"%s>%s</option>' % (e(k, quote=True), ' selected' if k == compte else '', e(v))
                   for k, v in sorted(membres.items(), key=lambda kv: kv[1].lower()))
    if compte not in membres:
        resp = '<option value="%s" selected>%s</option>' % (e(compte, quote=True), e(compte)) + resp
    from .fiche_ui5698 import options_roles   # 5.6.98 : mêmes groupes que l'onglet Parties (Clients, Parties adverses, Avocats, Autres intervenants)
    roles = options_roles('adverse')
    rattacher = args.get('emplacement') == 'rattacher'
    bloc_msg = ''
    if msg:
        bloc_msg = ('<fieldset class="d97-bloc"><legend>Courriel d’origine</legend><p>« %s » — reçu de %s</p>'
                    '<input type="hidden" name="message.boite" value="%s"><input type="hidden" name="message.uid" value="%s">'
                    '<input type="hidden" name="message.uidvalidity" value="%s"><input type="hidden" name="message.message_id" value="%s">'
                    '<input type="hidden" name="message.sujet" value="%s"><input type="hidden" name="message.expediteur" value="%s">'
                    '<p class="ax-muted">Le message sera rattaché au dossier créé. Rien n’est déplacé dans la boîte de courriel.</p>'
                    '<div class="d97-partie" data-d97-partie><label>Correspondant<input name="nom" value="%s"></label><label>Courriel<input name="courriel" value="%s" type="email"></label>'
                    '<label>Son rôle<select name="role">%s</select></label></div>'
                    '<p class="ax-muted">L’expéditeur n’est pas forcément le client : précisez son rôle.</p></fieldset>') % (
            e(msg['sujet']), e(msg['expediteur']), e(msg['boite'], quote=True), e(msg['uid'], quote=True), e(msg['uidvalidity'], quote=True),
            e(msg['message_id'], quote=True), e(msg['sujet'], quote=True), e(msg['expediteur'], quote=True), e(msg['expediteur_nom'] or msg['expediteur'], quote=True),
            e(msg['expediteur'], quote=True), options_roles('correspondant'))
    html = ('<section id="d97n" class="d97 d97-nouveau-page" aria-labelledby="d97n-titre">'
            '<p class="d97-ariane"><a href="%(liste)s">Dossiers</a> › <span aria-current="page">Nouveau dossier</span></p>'
            '<h1 id="d97n-titre">Nouveau dossier</h1>'
            '<form id="d97-nouveau" class="d97-form" data-d97-api="m57/dossiers" data-d97-apres="ouvrir" novalidate>'
            '<input type="hidden" name="cle" value="%(cle)s">%(msg)s'
            '<fieldset class="d97-bloc"><legend>L’essentiel</legend>'
            '<label class="d97-champ">Intitulé <span class="d97-requis">(obligatoire)</span><input name="titre" required maxlength="160" value="%(titre)s" autocomplete="off">'
            '<span class="d97-erreur" data-erreur="titre" role="alert"></span></label>'
            '<div class="d97-champ"><label for="d97-client">Client</label><input id="d97-client" name="client" maxlength="160" list="d97-clients" autocomplete="off">'
            '<label class="d97-case"><input type="checkbox" name="client_a_identifier" value="1"> À identifier</label>'
            '<span class="d97-erreur" data-erreur="client" role="alert"></span></div>'
            '<fieldset class="d97-radios"><legend>Nature</legend>%(natures)s<span class="d97-erreur" data-erreur="nature" role="alert"></span></fieldset>'
            '<label class="d97-champ">Responsable<select name="responsable">%(resp)s</select></label></fieldset>'
            '<details class="d97-bloc" %(ouvert)s><summary>Référence, membres et confidentialité, interlocuteurs, juridiction, emplacement</summary>'
            '<label class="d97-champ">Référence (révisable)<input name="reference" value="%(ref)s" maxlength="40" data-d97-ref>'
            '<span class="d97-aide">Proposée par le cabinet ; la collision est revérifiée à l’enregistrement.</span><span class="d97-erreur" data-erreur="reference" role="alert"></span></label>'
            '<fieldset class="d97-radios"><legend>Confidentialité</legend>'
            '<label><input type="radio" name="confidentialite" value="cabinet" checked> Tout le cabinet</label>'
            '<label><input type="radio" name="confidentialite" value="reserve"> Réservé aux membres choisis</label>'
            '<div class="d97-membres">%(membres)s</div></fieldset>'
            '<fieldset class="d97-parties"><legend>Parties et interlocuteurs</legend><div data-d97-parties>'
            '<div class="d97-partie" data-d97-partie><label>Nom<input name="nom" maxlength="160"></label><label>Courriel<input name="courriel" type="email"></label>'
            '<label>Rôle<select name="role">%(roles)s</select></label></div></div>'
            '<button type="button" class="ax-btn ghost" data-d97-ajouter-partie>Ajouter une partie</button><span class="d97-erreur" data-erreur="parties" role="alert"></span></fieldset>'
            '<div class="d97-deux"><label class="d97-champ">Juridiction<input name="juridiction" maxlength="160"></label>'
            '<label class="d97-champ">Numéro RG<input name="rg" maxlength="40" placeholder="24/01234"><span class="d97-erreur" data-erreur="rg" role="alert"></span></label></div>'
            '<fieldset class="d97-radios"><legend>Emplacement des documents</legend>'
            '<label><input type="radio" name="emplacement.mode" value="creer"%(creer)s> Créer le répertoire sous <strong>%(racine)s</strong></label>'
            '<p class="d97-aide" data-d97-emplacement>Emplacement proposé : <span data-d97-chemin>%(chemin)s</span></p>'
            '<label><input type="radio" name="emplacement.mode" value="rattacher"%(ratt)s> Rattacher un répertoire existant autorisé</label>'
            '<label class="d97-champ">Répertoire existant<input name="emplacement.chemin" placeholder="%(racine)s/…"></label>'
            '<span class="d97-erreur" data-erreur="emplacement" role="alert"></span>'
            '<p class="d97-aide">Si le stockage est hors ligne, le dossier est créé quand même ; les documents attendront avec un motif.</p></fieldset></details>'
            '<fieldset class="d97-bloc d97-conflits" data-d97-conflits><legend>Conflits d’intérêts</legend>'
            '<p class="d97-aide">La vérification compare le client et les parties aux dossiers connus du cabinet. Une vérification partielle ne conclut pas à l’absence de conflit.</p>'
            '<button type="button" class="ax-btn ghost" data-d97-verifier>Vérifier les conflits</button>'
            '<div class="d97-resultat" data-d97-resultat role="status" aria-live="polite"></div>'
            '<input type="hidden" name="conflit.id" value="">'
            '<div class="d97-decision" data-d97-decision hidden><fieldset class="d97-radios"><legend>Votre décision</legend>'
            '<label><input type="radio" name="conflit.decision" value="accepter"> Poursuivre : résultat examiné</label>'
            '<label><input type="radio" name="conflit.decision" value="a_etudier"> Mettre en attente (aucun acte tant que la décision n’est pas prise)</label></fieldset>'
            '<label class="d97-champ">Motif<textarea name="conflit.motif" rows="2" maxlength="500"></textarea></label>'
            '<span class="d97-erreur" data-erreur="conflit" role="alert"></span></div></fieldset>'
            '<div class="d97-actions"><button type="submit" class="ax-btn">Créer le dossier</button> <a class="ax-btn ghost" href="%(liste)s">Annuler</a>'
            '<p class="d97-statut" role="status" aria-live="polite"></p></div></form>'
            '<datalist id="d97-clients">%(clients)s</datalist></section>') % {
        'liste': _lien(prefix, '/dossiers'), 'cle': _cle(), 'msg': bloc_msg, 'titre': e(titre, quote=True), 'ref': e(reference, quote=True),
        'natures': ''.join('<label><input type="radio" name="nature" value="%s"%s> %s</label>' % (k, ' checked' if k == 'a_qualifier' else '', e(v))
                           for k, v in D.NATURES.items()),
        'resp': resp, 'ouvert': 'open' if (rattacher or msg) else '',
        'membres': ''.join('<label class="d97-case"><input type="checkbox" name="membres" value="%s"%s> %s</label>' % (
            e(k, quote=True), ' checked' if k == compte else '', e(v)) for k, v in sorted(membres.items(), key=lambda kv: kv[1].lower())),
        'roles': roles, 'racine': e(racine or 'la racine configurée'), 'chemin': e(D.nom_repertoire(titre or 'Intitulé', reference) if racine else ''),
        'creer': '' if rattacher else ' checked', 'ratt': ' checked' if rattacher else '',
        'clients': ''.join('<option value="%s">' % e(c, quote=True) for c in _clients_connus(desk, compte, role))}
    return html


def _clients_connus(desk, compte, role):
    """Clients déjà connus des dossiers que l'acteur voit (sélection rapide) ; jamais ceux d'un dossier réservé."""
    vus = []
    for d in D.lister(desk, (compte, role), {'statut': 'tous'}, 2000):
        if d.get('client') and d['client'] not in vus:
            vus.append(d['client'])
    return sorted(vus, key=str.lower)[:300]


# ------------------------------------------------------------------------------------------------ import des dossiers existants
def page_import(desk, env, auth, prefix, args):
    from .web567 import actor
    if actor(env)[1] != 'administrateur':
        raise Stop('role_insuffisant')
    r = D.migrer(desk, actor(env), simulation=True)
    lignes = ''.join('<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>' % (
        e(x['titre']), e(x['reference']), e(x['chemin']), e('Réservé' if x['confidentialite'] == 'reserve' else 'Cabinet')) for x in r['a_importer'][:500])
    conflits = ''.join('<li>%s : %s</li>' % (e(c['id']), e({'chemin_deja_rattache': 'répertoire déjà rattaché à un autre dossier',
                                                          'chemin_different': 'chemin différent dans le registre', 'identifiant_invalide': 'identifiant invalide',
                                                          'chemin_invalide': 'chemin invalide'}.get(c['motif'], c['motif']))) for c in r['conflits'])
    return ('<section class="d97" aria-labelledby="d97i-titre"><p class="d97-ariane"><a href="%s">Dossiers</a> › '
            '<span aria-current="page">Import des dossiers existants</span></p><h1 id="d97i-titre">Import des dossiers existants</h1>'
            '<p>Simulation : %d dossier(s) à importer, %d déjà importé(s), %d conflit(s). Rien n’a été écrit.</p>%s'
            '<div class="d97-tableau"><table class="d97-table"><caption>Dossiers à importer</caption><thead><tr><th scope="col">Dossier</th><th scope="col">Référence</th>'
            '<th scope="col">Emplacement</th><th scope="col">Confidentialité</th></tr></thead><tbody>%s</tbody></table></div>'
            '<form class="d97-form" data-d97-api="m57/dossiers/migration" data-d97-apres="recharger"><input type="hidden" name="cle" value="%s">'
            '<input type="hidden" name="simulation" value="non"><p>Les registres actuels sont d’abord sauvegardés ; l’import peut être relancé sans doublon.</p>'
            '<button class="ax-btn" type="submit"%s>Importer maintenant</button><p class="d97-statut" role="status"></p></form></section>') % (
        _lien(prefix, '/dossiers'), len(r['a_importer']), r['deja_importes'], len(r['conflits']),
        ('<h2>Conflits (non importés, aucune fusion)</h2><ul>%s</ul>' % conflits) if conflits else '', lignes or '<tr><td colspan="4">Aucun.</td></tr>',
        _cle(), '' if r['a_importer'] else ' disabled')


# ------------------------------------------------------------------------------------------------ § 12 : onglets de la fiche (registre)
def entete_fiche(desk, acteur, prefix, dossier_id):
    """Bandeau du registre en tête de la fiche existante : référence, client, nature, responsable, confidentialité, état du rangement,
    et la barre des six sections. Dossier hors registre (pas encore importé) : rien."""
    try:
        d = D.lire(desk, acteur, dossier_id)
    except Stop:
        return ''
    stock = d['stockage']
    rangement = ''
    if stock['etat'] != 'pret':
        rangement = ('<div class="d97-rangement" role="status"><p><strong>%s.</strong> %s</p>'
                     '<form class="d97-form" data-d97-api="m57/dossiers/%s/rangement" data-d97-apres="recharger"><input type="hidden" name="cle" value="%s">'
                     '<button class="ax-btn ghost" type="submit">Reprendre le rangement</button><span class="d97-statut" role="status"></span></form></div>') % (
            e(stock['libelle']), e(stock['motif'] or 'Le dossier est utilisable ; les documents seront rangés dès que le stockage répond.'), e(d['id'], quote=True), _cle())
    return ('<div class="d97-fiche-tete" data-d97-fiche="%s"><p class="d97-fiche-meta"><span>Réf. %s</span><span>%s</span><span>%s</span><span>Responsable : %s</span>'
            '<span>%s</span></p>%s</div>') % (
        e(d['id'], quote=True), e(d['reference']), e(('Client : ' + d['client']) if d['client'] else 'Client à identifier'), e(d['nature_libelle']),
        e(d['responsable'] or '—'), e(D.CONFIDENTIALITES.get(d['confidentialite'], '')), rangement)


def correspondances_html(desk, acteur, prefix, dossier_id):
    """Courriels classés à la main dans ce dossier (provenance, date, auteur du classement) ; lien vers le courriel conservé s'il existe."""
    try:
        rows = D.messages_du_dossier(desk, acteur, dossier_id)
    except Stop:
        return ''
    if not rows:
        return '<p class="ax-muted">Aucun courriel classé à la main dans ce dossier. Depuis Courriels : « Classer dans un dossier ».</p>'
    return '<ul class="d97-courriels">%s</ul>' % ''.join(
        '<li><strong>%s</strong> <span class="ax-muted">%s · %s · classé par %s (%s)</span></li>' % (
            e(r['sujet'] or '(sans objet)'), e(r['expediteur']), e(_date_fr(r['date_message']) or _date_fr(r['cree_le'])), e(r['auteur']),
            e('à la main' if r['provenance'] == 'manuel' else r['provenance'])) for r in rows)


def actions_fiche(prefix, dossier_id, titre):
    """Quatre actions de l'en-tête : Nouveau document, Ajouter (fichier, courriel, contact, tâche, rendez-vous, diligence), Confier à l'IA
    (icône commune déjà présente dans l'en-tête), et le menu « … » (droits, déplacement, export, archivage) ; chaque entrée ouvre le
    module existant sur ce dossier, aucune page n'est dupliquée."""
    ajouter = (('Fichier', '/documents', {'matter': dossier_id}), ('Courriel', '/courriels', {'dossier': dossier_id}),
               ('Contact', '/contacts', {'matter': dossier_id}), ('Tâche', '/mes-taches', {'matter': dossier_id}),
               ('Rendez-vous', '/planning', {'matter': dossier_id, 'nouveau': '1'}), ('Diligence', '/honoraires', {'matter': dossier_id}))
    autres = (('Droits et confidentialité', '/parametres/droits', {'dossier': dossier_id}), ('Déplacer ou rattacher le répertoire', '/dossiers/nouveau', {'emplacement': 'rattacher'}),
              ('Exporter', '/dossier', {'id': dossier_id, 'vue': 'documents'}), ('Préparer la clôture', '/dossier', {'id': dossier_id, 'vue': 'cloture'}),
              ('Archiver', '/dossiers', {'q': titre[:60]}))
    try:
        from .mission_web5697 import boutons_html   # « + Nouveau document » / « Nouvelle demande » du lot documents-mission
        boutons = boutons_html(prefix, dossier_id)
    except Exception:   # noqa: BLE001
        boutons = '<a class="ax-btn" href="%s">Nouveau document</a>' % _lien(prefix, '/nouveau-document', dossier=dossier_id)
    return ('<div class="d97-fiche-actions">%s'
            '<details class="d97-menu"><summary class="ax-btn ghost">Ajouter ▾</summary><div class="d97-menu-corps">%s</div></details>'
            '<details class="d97-menu"><summary class="ax-btn ghost" aria-label="Autres actions du dossier">…</summary><div class="d97-menu-corps">%s</div></details></div>') % (
        boutons,
        ''.join('<a href="%s">%s</a>' % (_lien(prefix, chemin, **params), e(libelle)) for libelle, chemin, params in ajouter),
        ''.join('<a href="%s">%s</a>' % (_lien(prefix, chemin, **params), e(libelle)) for libelle, chemin, params in autres))


def agenda_html(desk, prefix, dossier_id):
    """Agenda et tâches du dossier : dates confirmées (échéances, rendez-vous, audiences), dates proposées « À vérifier » comptées à part,
    tâches ouvertes du dossier. Ajouter ou ouvrir dans l'agenda : liens vers le module existant."""
    try:
        from .resume5659 import dates
        confirmees, proposees = dates(desk, dossier_id)
    except Exception:   # noqa: BLE001 — module des dates indisponible : état réel affiché, pas de faux zéro
        confirmees, proposees = None, 0
    try:
        from .taches5688 import open_tasks
        taches = [t for t in open_tasks(desk) if str(t.get('matter') or '') == str(dossier_id)]
    except Exception:   # noqa: BLE001
        taches = None
    lignes_dates = ('<p class="ax-muted">Dates indisponibles pour le moment.</p>' if confirmees is None else
                    ('<ul class="d97-liste">%s</ul>' % ''.join('<li><strong>%s%s</strong> — %s</li>' % (
                        e(_date_fr(x['date'])), (' ' + e(x['heure'])) if x.get('heure') else '', e(x['titre'])) for x in confirmees)
                     if confirmees else '<p class="ax-muted">Aucune date confirmée à venir.</p>'))
    a_verifier = ('<p><span class="d97-a-verifier">%d date%s proposée%s à vérifier</span> <a href="%s">Vérifier les échéances</a></p>' % (
        proposees, '' if proposees == 1 else 's', '' if proposees == 1 else 's', _lien(prefix, '/echeances', matter=dossier_id))) if proposees else ''
    lignes_taches = ('<p class="ax-muted">Tâches indisponibles pour le moment.</p>' if taches is None else
                     ('<ul class="d97-liste">%s</ul>' % ''.join('<li>%s%s</li>' % (e(str(t.get('title') or t.get('titre') or 'Tâche')),
                                                                                   (' — échéance ' + e(_date_fr(t.get('due')))) if t.get('due') else '') for t in taches[:50])
                      if taches else '<p class="ax-muted">Aucune tâche ouverte.</p>'))
    return ('<section class="d97-section" aria-labelledby="d97-agenda-t"><h2 id="d97-agenda-t">Agenda et tâches</h2>'
            '<p><a class="ax-btn" href="%s">Ajouter un rendez-vous</a> <a class="ax-btn ghost" href="%s">Ajouter une tâche</a> <a class="ax-btn ghost" href="%s">Ouvrir dans l’agenda</a></p>'
            '<h3>Dates confirmées</h3>%s%s<h3>Tâches</h3>%s</section>') % (
        _lien(prefix, '/planning', matter=dossier_id, nouveau='1'), _lien(prefix, '/mes-taches', matter=dossier_id), _lien(prefix, '/planning', matter=dossier_id),
        lignes_dates, a_verifier, lignes_taches)


def gestion_html(desk, acteur, prefix, dossier_id, intervenants=''):
    """Gestion : intervenants (rôle par dossier), conventions, temps, frais et facturation liés. Les pages de gestion gardent leurs droits
    propres : sans le droit « voir les honoraires », aucun montant ni lien n'est montré ici."""
    try:
        from .membres5650 import effective_rights
        droits = effective_rights(desk.c['state_dir'], acteur[0], acteur[1])
        honoraires = acteur[1] == 'administrateur' or droits.get('voir_honoraires', True) is not False
    except Stop:
        honoraires = False
    liens = [('Contacts du dossier', '/contacts', {'matter': dossier_id})]
    if honoraires:
        liens += [('Temps et diligences', '/honoraires', {'matter': dossier_id}), ('Conventions et facturation', '/facturation', {'matter': dossier_id})]
    return ('<section class="d97-section" aria-labelledby="d97-gestion-t"><h2 id="d97-gestion-t">Gestion</h2>%s'
            '<p class="d97-liens">%s</p>%s</section>') % (
        intervenants, ' '.join('<a class="ax-btn ghost" href="%s">%s</a>' % (_lien(prefix, chemin, **params), e(libelle)) for libelle, chemin, params in liens),
        '' if honoraires else '<p class="ax-muted">Les honoraires de ce dossier relèvent d’un droit que votre compte n’a pas.</p>')
