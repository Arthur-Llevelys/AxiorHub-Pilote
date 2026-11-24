"""5.6.89 : routes ``/api440/m5689/politique/*`` et ``/api440/m5689/budget/*``, et page Paramètres › Données et budget de l'IA.

Le compte et le rôle viennent de la requête authentifiée. Un champ ``allowed``, ``role``, ``acteur`` ou ``cabinet_id`` envoyé par le navigateur n'est jamais lu.
Une route d'évaluation visible ne dispense pas le service d'effet de refaire son contrôle : ``data_policy5689`` et ``budget_ledger5689`` s'en chargent au moment de l'effet.
"""
from html import escape as e

from .common import Stop

LECTURES = frozenset(('politique/etat', 'politique/mission', 'budget/mission'))
ECRITURE_TOUS = frozenset(('politique/evaluer',))
ADMIN_GET = frozenset(('politique/prestataires', 'politique/flux', 'budget/etat', 'budget/bilan', 'budget/bascule'))
ADMIN_POST = frozenset(('politique/enregistrer', 'politique/prestataire', 'budget/reservations', 'budget/rapprochement', 'budget/plafonds', 'budget/credit', 'budget/cout',
                        'budget/depassement/acquitter', 'budget/bascule/retour'))
ADMIN = ADMIN_GET | ADMIN_POST
MESSAGES_CONFLIT = {'version_en_conflit': 'Cette page n’est plus à jour : rechargez-la avant d’enregistrer.'}
# 5.6.109 (lot M, F18) : relevé « Consommation et coûts », ``/api440/m56109/conso/*``. Lecture seule (GET) : ``releve`` (JSON), ``vue``
# (fragment de la section des paramètres), ``export`` (CSV filtré). Tous les rôles : chacun ses propres demandes, l'administrateur tout le
# cabinet (portée décidée par ``consommation56109.filtres``, jamais par un paramètre). Hors de ``m5689/budget/`` : ce préfixe est réservé
# à l'administrateur par le contrôle central, GET compris.
ROUTES_56109 = ('releve', 'vue', 'export')
PRIVE = [('Cache-Control', 'private, no-store')]
MESSAGES_56109 = {'introuvable': 'Élément introuvable.', 'controle_indisponible': 'Le contrôle des droits est indisponible : rien n’est affiché plutôt qu’un accès élargi.',
                  'releve_indisponible': 'Le relevé n’a pas pu être lu : rien n’est exporté plutôt qu’un faux zéro.',
                  'periode_invalide': 'Période non reconnue : choisissez aujourd’hui, sept jours, le mois ou deux dates (400 jours au plus).',
                  'filtre_invalide': 'Filtre non reconnu.', 'devise_invalide': 'Devise non reconnue : indiquez son code à trois lettres (USD, EUR).',
                  'methode_refusee': 'Cette adresse ne sert qu’à lire le relevé.', 'route_inconnue': 'Élément introuvable.',
                  'unite_service_invalide': 'Unité de service non reconnue.'}
STATUTS_56109 = {'introuvable': '404 Not Found', 'route_inconnue': '404 Not Found', 'controle_indisponible': '503 Service Unavailable', 'releve_indisponible': '503 Service Unavailable',
                 'role_insuffisant': '403 Forbidden', 'methode_refusee': '405 Method Not Allowed'}


def allowed(role, route):
    route = route.split('?', 1)[0]
    if route in ADMIN:
        return role == 'administrateur'
    return route in LECTURES or route in ECRITURE_TOUS


def _erreur(error):
    from .budget_ledger5689 import MESSAGES, AttenteBudget
    from .data_policy5689 import MOTIFS
    from .erreurs5634 import explain
    from .web440 import json_out
    if isinstance(error, AttenteBudget):
        return json_out(error.vue())
    code = str(error).split(':')[0]
    message = {**MOTIFS, **MESSAGES, **MESSAGES_CONFLIT, **MESSAGES_56109}.get(code) or explain(code, getattr(error, 'detail', None))
    return json_out({'error': code, 'message': message}, '403 Forbidden' if code in ('role_insuffisant', 'dossier_reserve') else ('409 Conflict' if code == 'version_en_conflit' else '400 Bad Request'))


def api_consommation(env, desk, auth, prefix, name, args, method):
    """5.6.109 (F18) : ``/api440/m56109/conso/{releve,vue,export}``. Le compte et le rôle viennent de la requête authentifiée ; les droits
    passent avant toute donnée. Dossier inconnu ou réservé : même 404 neutre ; contrôle des droits en panne : 503, jamais un accès élargi.
    Aucune écriture métier : seul l'export laisse une ligne au journal d'audit (qui a exporté quoi), sans aucune valeur du relevé."""
    from .web440 import json_out
    from .web567 import actor
    owner, role = actor(env)
    route = name[len('m56109/conso/'):].split('?', 1)[0]
    try:
        if route not in ROUTES_56109:
            raise Stop('route_inconnue')
        if method not in ('GET', 'HEAD'):
            raise Stop('methode_refusee')
        if role not in ('administrateur', 'avocat', 'assistant'):
            raise Stop('role_insuffisant')
        from . import consommation56109 as conso
        if route == 'releve':
            return {**json_out(conso.releve(desk, owner, role, args or {})), 'headers': list(PRIVE)}
        if route == 'vue':
            from .ia5682 import vue_consommation
            return {'status': '200 OK', 'kind': 'text/html; charset=utf-8', 'body': vue_consommation(desk, owner, role, args or {}, prefix), 'headers': list(PRIVE)}
        try:
            from .membres5650 import effective_rights
            droits = effective_rights(desk.c['state_dir'], owner, role)
        except Stop:
            raise Stop('controle_indisponible') from None
        if not droits.get('exporter'):
            raise Stop('role_insuffisant')
        nom, texte, lignes = conso.export_csv(desk, owner, role, args or {})
        f = conso.filtres(desk, owner, role, args or {})
        desk.audit('consommation56109_export', {'periode': f['periode']['code'], 'debut': f['periode']['debut'], 'fin': f['periode']['fin'], 'lignes': lignes,
                                                'portee': 'cabinet' if f['admin'] and not f['utilisateur'] else 'personnelle',
                                                'filtres': sorted(k for k in conso.FILTRES if f[k] and not (k == 'utilisateur' and not f['admin']))})
        return {'status': '200 OK', 'kind': 'text/csv; charset=utf-8', 'body': texte, 'headers': [('Content-Disposition', 'attachment; filename="%s"' % nom)] + list(PRIVE)}
    except Stop as error:
        code = str(error).split(':')[0]
        from .erreurs5634 import explain
        return {**json_out({'error': 'introuvable' if code == 'route_inconnue' else code, 'message': MESSAGES_56109.get(code) or explain(code, getattr(error, 'detail', None))},
                           STATUTS_56109.get(code, '400 Bad Request')), 'headers': list(PRIVE)}


def api(env, desk, auth, prefix, name, args, method):
    try:
        return _route(env, desk, auth, prefix, name, args, method)
    except Stop as error:
        return _erreur(error)


def _liste(valeur):
    if isinstance(valeur, (list, tuple)):
        return [str(x).strip() for x in valeur if str(x).strip()]
    return [x.strip() for x in str(valeur or '').replace(';', ',').split(',') if x.strip()]


def _evaluer(desk, data, owner, role):
    """Le navigateur désigne ce qu'il veut faire ; il n'annonce aucun droit. Une destination inconnue du serveur est externe."""
    from . import data_policy5689 as dp
    from .ai_gateway import provider_registry
    destination = str(data.get('destination') or '')[:120]
    fiche = provider_registry(desk.c).get(destination)
    local = destination in dp.LOCAUX or bool(fiche and fiche.get('type', 'ollama') == 'ollama')
    dossier = str(data.get('dossier') or '')[:120]
    decision = dp.evaluer(desk.c, str(data.get('outil') or ''), destination, finalite=str(data.get('finalite') or '')[:60], dossiers=(dossier,) if dossier else (), labels=_liste(data.get('labels'))[:20],
                          region=None, acteur=owner, role=role, requete=str(data.get('requete') or '')[:2000], local=local, source_refs=_liste(data.get('source_refs'))[:50])
    return {**dp.contrat(decision), 'motifs': [dp.MOTIFS.get(x.split(':')[0], x) for x in decision['denial_reasons']], 'mode': decision['mode'],
            'avis': 'Évaluation indicative : le contrôle est refait au moment de l’accès ou du transfert.'}


def _mission_lisible(desk, ident, owner, role):
    """Synthèse et coût d'une mission : mêmes droits que son dossier. Destinations et montants sont des données du dossier ; un compte écarté n'en lit rien."""
    import json
    import sqlite3
    from .arret5643 import matter_of
    from .membres5650 import hidden_for
    try:
        row = desk.db.execute('SELECT args FROM jobs WHERE id=?', (int(ident),)).fetchone()
    except (ValueError, TypeError, sqlite3.Error):
        row = None
    if row:
        try:
            matter = matter_of(json.loads(row[0] or '{}'))
        except ValueError:
            raise Stop('dossier_reserve') from None        # dossier indéterminable : on ne présume pas le droit
        if matter and matter in (hidden_for(desk.c['state_dir'], owner, role) or ()):
            raise Stop('dossier_reserve')
    return str(ident)


def _route(env, desk, auth, prefix, name, args, method):
    from . import budget_ledger5689 as bl, data_policy5689 as dp, passerelle5689
    from .web440 import check_post, json_out
    from .web567 import actor
    owner, role = actor(env)
    route = name[len('m5689/'):].split('?', 1)[0]
    if not allowed(role, route):
        raise Stop('role_insuffisant')
    if method == 'GET':
        if route == 'politique/etat':
            return json_out(dp.etat_public(desk))
        if route == 'politique/mission':
            return json_out(dp.synthese_mission(desk, _mission_lisible(desk, args.get('id') or '', owner, role)))
        if route == 'politique/prestataires':
            return json_out({'prestataires': dp.synchroniser(desk), 'limites_extensions': passerelle5689.limites_extensions()})
        if route == 'politique/flux':
            return json_out({'flux': passerelle5689.flux(desk)})
        if route == 'budget/etat':
            return json_out(bl.etat(desk))
        if route == 'budget/bilan':
            return json_out(bl.bilan_mensuel(desk, args.get('mois')))
        if route == 'budget/mission':
            return json_out(bl.mission(desk, _mission_lisible(desk, args.get('id') or '', owner, role)))
        if route == 'budget/bascule':
            return json_out(bl.bascule(desk))
        raise Stop('methode_non_autorisee')
    data = check_post(env, auth)
    if route == 'politique/evaluer':
        return json_out(_evaluer(desk, data, owner, role))
    if route == 'politique/enregistrer':
        return json_out(dp.enregistrer(desk, {**data, 'regions_admises': _liste(data.get('regions_admises')), 'categories_fermees': _liste(data.get('categories_fermees'))}, owner))
    if route == 'politique/prestataire':
        return json_out(dp.enregistrer_prestataire(desk, {**data, 'categories': _liste(data.get('categories')), 'finalites': _liste(data.get('finalites'))}, owner))
    if route == 'budget/reservations':
        return json_out(bl.reserver_service(desk, data, owner))
    if route == 'budget/rapprochement':
        return json_out(bl.rapprocher(desk.c['state_dir'], str(data.get('call_id') or ''), data.get('montant'), str(data.get('devise') or 'USD')[:3].upper(), data.get('taux'), data.get('date_taux'), str(data.get('reference') or '')))
    if route == 'budget/plafonds':
        return json_out(bl.enregistrer_plafonds(desk, data, owner, data.get('version')))
    if route == 'budget/credit':
        return json_out(bl.ajouter_credit(desk, data, owner))
    if route == 'budget/cout':
        return json_out(bl.saisir_cout(desk, data, owner))
    if route == 'budget/depassement/acquitter':
        return json_out(bl.acquitter(desk, data.get('id'), owner))
    if route == 'budget/bascule/retour':
        return json_out(bl.retour_arriere(desk, owner))
    raise Stop('route_inconnue')


# ------------------------------------------------------------------------------------------------ page
def _usd(micro):
    if micro is None:
        return 'Inconnu'
    from .budget_ledger5689 import unites
    return ('%.2f $' % unites(micro)).replace('.', ',')


def _montant(micro, devise='USD'):
    """5.6.109 (F18) : montant dans sa devise réelle. Le dollar garde son signe ; une autre devise garde son code à trois lettres : un
    coût saisi en euros ne s'affiche jamais avec le signe du dollar, et n'est converti par personne."""
    if micro is None or devise in ('USD', '', None):
        return _usd(micro)
    from .budget_ledger5689 import unites
    return ('%.2f ' % unites(micro)).replace('.', ',') + str(devise)


def _lien_consommation(prefix):
    """Accès direct à la section unique « Consommation et coûts » (Paramètres › Modèles d'IA) : même registre, même calcul."""
    return ('<p><a href="%s">Consommation et coûts : relevé par période, fournisseur, modèle et mission</a></p>'
            % e((prefix or '') + '/parametres?rubrique=ia#consommation-couts', quote=True))


def _section_politique(desk):
    from . import data_policy5689 as dp
    etat = dp.etat_public(desk)
    alerte = ''
    if not etat['lisible']:
        alerte = '<div role="alert"><p><strong>%s</strong></p><p>Choisissez une politique ci-dessous ou restaurez la dernière sauvegarde : tant qu’elle reste illisible, aucun nouvel envoi n’est fait.</p></div>' % e(etat['message'])
        etat = {'mode': '', 'explicite': False, 'libelle': 'Illisible', 'revision': 0, 'perimetre': '', 'regions_admises': [], 'categories_fermees': [], 'qualite_minimale': dp.DEFAUT_QUALITE, 'repli_externe': False, 'confidentiel_local': False}
    options = ''.join('<option value="%s"%s>%s</option>' % (m, ' selected' if etat['mode'] == m and etat['explicite'] else '', e(dp.LIBELLES_MODE[m])) for m in dp.MODES)
    origine = '' if etat['explicite'] else '<p class="ax-muted">Aucun mode n’a encore été choisi : les réglages d’origine continuent de s’appliquer. Pour les recherches, le connecteur Claude et la voix, les nouvelles règles commencent à ce choix.</p>'
    return ('<section class="ax-card"><h2>Politique des données</h2>' + alerte + '<p role="status">En vigueur : %s (révision %d).</p>%s'
            '<form class="m5-form" data-api="m5689/politique/enregistrer" data-reload><input type="hidden" name="version" value="%d">'
            '<label class="m5-field">Mode<select name="mode">%s</select></label>'
            '<label class="m5-field">Périmètre déclaré (obligatoire en local strict)<input name="perimetre" maxlength="400" value="%s"></label>'
            '<label class="m5-field">Lieux de traitement admis (séparés par des virgules ; vide : pas d’exigence)<input name="regions_admises" value="%s"></label>'
            '<label class="m5-field">Catégories fermées en plus de « Ce que l’IA peut voir »<input name="categories_fermees" value="%s"></label>'
            '<label class="m5-field">Note minimale de qualité au banc d’essai (0 à 100)<input name="qualite_minimale" type="number" min="0" max="100" value="%d"></label>'
            '<label class="m5-field"><span><input type="checkbox" name="repli_externe" class="m5-check"%s> Autoriser un fournisseur externe quand le modèle local est en panne (hors local strict)</span></label>'
            '<label class="m5-field"><span><input type="checkbox" name="confidentiel_local" class="m5-check"%s> Garder les correspondances confidentielles sur le modèle local</span></label>'
            '<button class="ax-btn" type="submit">Enregistrer la politique</button><p role="status" class="m5-status"></p></form></section>') % (
        e(etat['libelle']), etat['revision'], origine, etat['revision'], options, e(etat['perimetre'], quote=True), e(', '.join(etat['regions_admises']), quote=True),
        e(', '.join(etat['categories_fermees']), quote=True), etat['qualite_minimale'], ' checked' if etat['repli_externe'] else '', ' checked' if etat['confidentiel_local'] else '')


def _form_prestataire(r=None):
    """Fiche préremplie : modifier un champ n'efface pas les autres ; une valeur vide reste inconnue."""
    r = r or {}

    def champ(nom, libelle, longueur=200):
        return '<label class="m5-field">%s<input name="%s" maxlength="%d" value="%s"></label>' % (e(libelle), nom, longueur, e(str(r.get(nom) or ''), quote=True))
    ident = ('<input type="hidden" name="id" value="%s"><input type="hidden" name="version" value="%s">' % (e(r['id'], quote=True), e(str(r.get('revision') or ''), quote=True))) if r else champ('id', 'Identifiant', 80)
    return ('<form class="m5-form" data-api="m5689/politique/prestataire" data-reload>%s%s%s%s%s%s%s%s'
            '<label class="m5-field">Nature<select name="nature"><option value="prestataire"%s>Prestataire</option><option value="service_cabinet"%s>Service privé du cabinet</option></select></label>'
            '<label class="m5-field">Catégories envoyées (séparées par des virgules)<input name="categories" value="%s"></label><label class="m5-field">Finalités<input name="finalites" value="%s"></label>%s'
            '<label class="m5-field"><span><input type="checkbox" name="admis" class="m5-check"%s> Admis par le cabinet</span></label>'
            '<label class="m5-field"><span><input type="checkbox" name="public" class="m5-check"%s> Source de droit public (admise en local strict pour une requête générique)</span></label>'
            '<button class="ax-btn" type="submit">Enregistrer le prestataire</button><p role="status" class="m5-status"></p></form>') % (
        ident, champ('nom', 'Nom', 120), champ('hote', 'Hôte'), champ('region_inference', 'Lieu d’inférence'), champ('region_stockage', 'Lieu de stockage'), champ('region_sauvegarde', 'Lieu des sauvegardes'),
        champ('conservation', 'Conservation'), champ('entrainement', 'Entraînement sur les données'), '' if r.get('nature') == 'service_cabinet' else ' selected', ' selected' if r.get('nature') == 'service_cabinet' else '',
        e(', '.join(r.get('categories') or []), quote=True), e(', '.join(r.get('finalites') or []), quote=True),
        champ('preuve', 'Preuve (contrat, page, date)', 400), ' checked' if r.get('admis') else '', ' checked' if r.get('public') else '')


def _section_prestataires(desk):
    from . import data_policy5689 as dp, passerelle5689
    lignes = dp.synchroniser(desk)
    corps = ''.join('<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>' % (
        e(r['nom']), 'Admis' if r['admis'] else 'Non admis', e(r['region_inference_affichage']), e(r['region_stockage_affichage']), e(r['conservation_affichage']), e(r['entrainement_affichage'])) for r in lignes)
    fiches = ''.join('<details><summary>Modifier %s</summary>%s</details>' % (e(r['nom']), _form_prestataire(r)) for r in lignes)
    limites = ''.join('<li>%s</li>' % e(x) for x in passerelle5689.limites_extensions())
    flux = ''.join('<li>%s · %s · %s · %d</li>' % (e(r['hote']), e(r['categorie']), e(r['decision']), r['nombre']) for r in passerelle5689.flux(desk))
    return ('<section class="ax-card"><h2>Prestataires</h2><p class="ax-muted">Une valeur non renseignée reste inconnue : ce n’est ni une promesse de localisation, ni une absence de conservation. Le lieu de stockage ne prouve pas celui de l’inférence ou de la sauvegarde.</p>'
            '<div class="table"><table><thead><tr><th>Prestataire</th><th>État</th><th>Lieu d’inférence</th><th>Lieu de stockage</th><th>Conservation</th><th>Entraînement</th></tr></thead><tbody>%s</tbody></table></div>'
            '%s<details><summary>Ajouter un prestataire</summary>%s</details>'
            '<h3>Ce qui est imposé aux extensions</h3><ul>%s</ul><details><summary>Destinations observées (30 jours)</summary><ul>%s</ul></details></section>') % (corps, fiches, _form_prestataire(), limites, flux or '<li>Aucune destination observée.</li>')


def _section_budget(desk, prefix=''):
    from . import budget_ledger5689 as bl
    etat = bl.etat(desk)
    reglage = desk.settings(bl.SETTING, {}) or {}
    reglage = reglage if isinstance(reglage, dict) else {}
    lignes = ''.join('<tr><td>%s%s</td><td>%s</td><td>%s</td></tr>' % (e(r['libelle']), (' : <strong>%s</strong>' % e(r['cle'])) if r.get('cle') and r['perimetre'] == 'fournisseur' else '',
                                                                       _usd(r.get('plafond_micro')) if 'erreur' not in r else 'Illisible', _usd(r.get('engage_micro')) if 'erreur' not in r else 'Inconnu') for r in etat['plafonds'])
    depassements = ''.join(('<li>%s, %s : %s au-delà de l’estimation. <form class="m5-form m5-inline m5-mini" data-api="m5689/budget/depassement/acquitter" data-reload><input type="hidden" name="id" value="%s">'
                            '<button class="ax-btn ghost" type="submit">Marquer comme contrôlé</button></form></li>') % (e(r['provider']), e(r['model']), _usd(r['micro']), e(r['id'], quote=True)) for r in etat['depassements_ouverts'])
    # 5.6.109 (C12) : une ligne incertaine n'est pas toujours un appel coupé : un appel abouti sans usage ou sans tarif reste incertain lui aussi
    incertain = ('<p role="status"><strong>%d appel(s) sans montant certain</strong> (coupés après l’envoi, ou aboutis sans usage ni tarif fourni) restent incertains pour %s : ce montant reste compté tant qu’il n’est pas rapproché.</p>' % (etat['incertains'], _usd(etat['incertains_micro']))) if etat['incertains'] else ''
    return ('<section class="ax-card" id="budget"><h2>Plafonds de l’IA</h2><p class="ax-muted">Ces plafonds limitent ce qu’AxiorHub engage. L’estimation par la taille du texte n’est pas une garantie de facture, et les autres logiciels utilisant le même compte fournisseur restent hors de ce plafond.</p>%s%s'
            '<div class="table"><table><thead><tr><th>Plafond</th><th>Limite</th><th>Engagé ce mois</th></tr></thead><tbody>%s</tbody></table></div>%s'
            '<form class="m5-form" data-api="m5689/budget/plafonds" data-reload><input type="hidden" name="version" value="%d">'
            '<label class="m5-field">Plafond mensuel du cabinet, en dollars (vide : aucun)<input name="mois_usd" inputmode="decimal" value="%s"></label>'
            '<label class="m5-field">Plafond annuel du cabinet, en dollars (vide : aucun)<input name="cabinet_usd" inputmode="decimal" value="%s"></label>'
            '<button class="ax-btn" type="submit">Enregistrer les plafonds</button><p role="status" class="m5-status"></p></form></section>') % (
        _lien_consommation(prefix), incertain, lignes or '<tr><td colspan="3">Aucun plafond fixé.</td></tr>', ('<h3>Dépassements à contrôler</h3><ul>%s</ul>' % depassements) if depassements else '', int(reglage.get('revision') or 0),
        e(str(reglage.get('mois_usd') or ''), quote=True), e(str(reglage.get('cabinet_usd') or ''), quote=True))


def _section_bilan(desk, mois):
    """5.6.109 (F18) : chaque montant s'affiche dans sa devise ; le total reste celui des dollars, et les autres devises sont listées à
    part, sans conversion. La saisie d'un coût fixe dit sa devise (dollar ou euro) : le signe n'est jamais changé après coup."""
    from . import budget_ledger5689 as bl
    b = bl.bilan_mensuel(desk, mois)

    def montant(r):
        texte = _montant(r['montant_micro'], r.get('devise') or 'USD')
        if r['categorie'] == 'api':        # ce qui n'est pas confirmé reste à part : ni dans le montant, ni dans le total
            a_part = [(libelle, r.get(cle)) for cle, libelle in (('estime_micro', 'estimé sans mesure'), ('en_cours_micro', 'en cours'), ('incertain_micro', 'incertain')) if r.get(cle)]
            if a_part:
                texte += ' <small>à part :</small> ' + ' · '.join('<small>%s</small> %s' % (libelle, _usd(v)) for libelle, v in a_part)
        return texte
    lignes = ''.join('<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>' % (e(r['libelle']), montant(r), {'connu': 'Connu', 'estime': 'Estimé', 'provisoire': 'Provisoire', 'inconnu': 'Inconnu'}.get(r['etat'], e(r['etat'])), e(r['note'] or '')) for r in b['lignes'])
    categories = ''.join('<option value="%s">%s</option>' % (c, e(l)) for c, l in bl.BILAN if c != 'api')
    credits_ = ''.join('<li>%s %s : %s%s</li>' % ('Crédit' if r['nature'] == 'credit' else 'Remise', e(r['provider']), _montant(r['amount_micro'], r.get('currency') or 'USD'), (' (expire le %s)' % e(r['expires_at'])) if r['expires_at'] else '') for r in b['credits'])
    total = 'Total des montants connus : %s%s' % (_usd(b['total_connu_micro']), '' if b['total_complet'] else ' (incomplet : des catégories restent inconnues)')
    autres = sorted((d, m) for d, m in (b.get('totaux_par_devise') or {}).items() if d != 'USD')
    devises = ('<p role="status">Autres devises, non converties ni additionnées : %s</p>' % ' · '.join('<strong>%s</strong>' % e(_montant(m, d)) for d, m in autres)) if autres else ''
    return ('<section class="ax-card" id="bilan"><h2>Coût du mois %s</h2><div class="table"><table><thead><tr><th>Catégorie</th><th>Montant</th><th>État</th><th>Note</th></tr></thead><tbody>%s</tbody></table></div>'
            '<p role="status"><strong>%s</strong></p>' + devises.replace('%', '%%') + '%s<ul>%s</ul>'
            '<form class="m5-form" data-api="m5689/budget/cout" data-reload><input type="hidden" name="mois" value="%s"><label class="m5-field">Catégorie<select name="categorie">%s</select></label>'
            '<label class="m5-field">Montant du mois<input name="montant" inputmode="decimal"></label>'
            '<label class="m5-field">Devise du montant<select name="devise"><option value="USD">Dollar (USD)</option><option value="EUR">Euro (EUR)</option></select></label>'
            '<label class="m5-field">Note<input name="note" maxlength="400"></label>'
            '<button class="ax-btn" type="submit">Enregistrer ce coût</button><p role="status" class="m5-status"></p></form></section>') % (
        e(b['mois']), lignes, e(total), ('<h3>Crédits et remises (usage brut inchangé)</h3><ul>%s</ul>' % credits_) if credits_ else '', ''.join('<li>%s</li>' % e(x) for x in b['limites']), e(b['mois'], quote=True), categories)


def page(desk, auth, prefix, env, args):
    from .web567 import actor
    if actor(env)[1] != 'administrateur':
        raise Stop('role_insuffisant')
    return ('<h1>Données et budget de l’IA</h1><p class="ax-muted">Ce qui peut quitter le cabinet, vers quels services, et ce que cela coûte. Tout est contrôlé sur le serveur, au moment de l’accès ou du transfert.</p>'
            + _section_politique(desk) + _section_prestataires(desk) + _section_budget(desk, prefix) + _section_bilan(desk, args.get('mois')))
