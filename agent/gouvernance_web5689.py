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
    message = {**MOTIFS, **MESSAGES, **MESSAGES_CONFLIT}.get(code) or explain(code, getattr(error, 'detail', None))
    return json_out({'error': code, 'message': message}, '403 Forbidden' if code in ('role_insuffisant', 'dossier_reserve') else ('409 Conflict' if code == 'version_en_conflit' else '400 Bad Request'))


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


def _section_budget(desk):
    from . import budget_ledger5689 as bl
    etat = bl.etat(desk)
    reglage = desk.settings(bl.SETTING, {}) or {}
    reglage = reglage if isinstance(reglage, dict) else {}
    lignes = ''.join('<tr><td>%s%s</td><td>%s</td><td>%s</td></tr>' % (e(r['libelle']), (' : ' + e(r['cle'])) if r.get('cle') and r['perimetre'] == 'fournisseur' else '',
                                                                       _usd(r.get('plafond_micro')) if 'erreur' not in r else 'Illisible', _usd(r.get('engage_micro')) if 'erreur' not in r else 'Inconnu') for r in etat['plafonds'])
    depassements = ''.join(('<li>%s, %s : %s au-delà de l’estimation. <form class="m5-form m5-inline m5-mini" data-api="m5689/budget/depassement/acquitter" data-reload><input type="hidden" name="id" value="%s">'
                            '<button class="ax-btn ghost" type="submit">Marquer comme contrôlé</button></form></li>') % (e(r['provider']), e(r['model']), _usd(r['micro']), e(r['id'], quote=True)) for r in etat['depassements_ouverts'])
    incertain = ('<p role="status"><strong>%d appel(s) coupé(s) après envoi</strong> restent incertains pour %s : ce montant reste compté tant qu’il n’est pas rapproché.</p>' % (etat['incertains'], _usd(etat['incertains_micro']))) if etat['incertains'] else ''
    return ('<section class="ax-card" id="budget"><h2>Plafonds de l’IA</h2><p class="ax-muted">Ces plafonds limitent ce qu’AxiorHub engage. L’estimation par la taille du texte n’est pas une garantie de facture, et les autres logiciels utilisant le même compte fournisseur restent hors de ce plafond.</p>%s'
            '<div class="table"><table><thead><tr><th>Plafond</th><th>Limite</th><th>Engagé ce mois</th></tr></thead><tbody>%s</tbody></table></div>%s'
            '<form class="m5-form" data-api="m5689/budget/plafonds" data-reload><input type="hidden" name="version" value="%d">'
            '<label class="m5-field">Plafond mensuel du cabinet, en dollars (vide : aucun)<input name="mois_usd" inputmode="decimal" value="%s"></label>'
            '<label class="m5-field">Plafond annuel du cabinet, en dollars (vide : aucun)<input name="cabinet_usd" inputmode="decimal" value="%s"></label>'
            '<button class="ax-btn" type="submit">Enregistrer les plafonds</button><p role="status" class="m5-status"></p></form></section>') % (
        incertain, lignes or '<tr><td colspan="3">Aucun plafond fixé.</td></tr>', ('<h3>Dépassements à contrôler</h3><ul>%s</ul>' % depassements) if depassements else '', int(reglage.get('revision') or 0),
        e(str(reglage.get('mois_usd') or ''), quote=True), e(str(reglage.get('cabinet_usd') or ''), quote=True))


def _section_bilan(desk, mois):
    from . import budget_ledger5689 as bl
    b = bl.bilan_mensuel(desk, mois)
    lignes = ''.join('<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>' % (e(r['libelle']), _usd(r['montant_micro']), {'connu': 'Connu', 'estime': 'Estimé', 'provisoire': 'Provisoire', 'inconnu': 'Inconnu'}.get(r['etat'], e(r['etat'])), e(r['note'] or '')) for r in b['lignes'])
    categories = ''.join('<option value="%s">%s</option>' % (c, e(l)) for c, l in bl.BILAN if c != 'api')
    credits_ = ''.join('<li>%s %s : %s%s</li>' % ('Crédit' if r['nature'] == 'credit' else 'Remise', e(r['provider']), _usd(r['amount_micro']), (' (expire le %s)' % e(r['expires_at'])) if r['expires_at'] else '') for r in b['credits'])
    total = 'Total des montants connus : %s%s' % (_usd(b['total_connu_micro']), '' if b['total_complet'] else ' (incomplet : des catégories restent inconnues)')
    return ('<section class="ax-card" id="bilan"><h2>Coût du mois %s</h2><div class="table"><table><thead><tr><th>Catégorie</th><th>Montant</th><th>État</th><th>Note</th></tr></thead><tbody>%s</tbody></table></div>'
            '<p role="status"><strong>%s</strong></p>%s<ul>%s</ul>'
            '<form class="m5-form" data-api="m5689/budget/cout" data-reload><input type="hidden" name="mois" value="%s"><label class="m5-field">Catégorie<select name="categorie">%s</select></label>'
            '<label class="m5-field">Montant du mois, en dollars<input name="montant" inputmode="decimal"></label><label class="m5-field">Note<input name="note" maxlength="400"></label>'
            '<button class="ax-btn" type="submit">Enregistrer ce coût</button><p role="status" class="m5-status"></p></form></section>') % (
        e(b['mois']), lignes, e(total), ('<h3>Crédits et remises (usage brut inchangé)</h3><ul>%s</ul>' % credits_) if credits_ else '', ''.join('<li>%s</li>' % e(x) for x in b['limites']), e(b['mois'], quote=True), categories)


def page(desk, auth, prefix, env, args):
    from .web567 import actor
    if actor(env)[1] != 'administrateur':
        raise Stop('role_insuffisant')
    return ('<h1>Données et budget de l’IA</h1><p class="ax-muted">Ce qui peut quitter le cabinet, vers quels services, et ce que cela coûte. Tout est contrôlé sur le serveur, au moment de l’accès ou du transfert.</p>'
            + _section_politique(desk) + _section_prestataires(desk) + _section_budget(desk) + _section_bilan(desk, args.get('mois')))
