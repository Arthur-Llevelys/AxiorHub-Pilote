"""5.6.89 : routes ``/api440/m5689/contentieux/*`` et page « Contentieux » du dossier.

Le compte et le rôle viennent de la requête authentifiée, jamais du corps. Chaque écriture passe par ``check_post`` ; les services refont leurs contrôles (dossier réservé, rôle, version attendue, idempotence) :
une route par identifiant d'objet ne passe pas par le contrôle central des clés de requête. La page est en HTML sobre ; le lot Carbon l'habillera.
"""
from html import escape as e
import uuid

from .common import Stop

LECTURES = frozenset(('contentieux/matrice', 'contentieux/paquet-contentieux', 'contentieux/pieces', 'contentieux/doublons', 'contentieux/categories', 'contentieux/actes', 'contentieux/actes/vue', 'contentieux/delais', 'contentieux/regles', 'contentieux/coherence',
                      'contentieux/audiences', 'contentieux/audiences/vue', 'contentieux/paquets', 'contentieux/paquets/vue', 'contentieux/paquets/export'))
ECRITURES = frozenset(('contentieux/matrice/pretention', 'contentieux/matrice/element', 'contentieux/matrice/amorcer', 'contentieux/matrice/ia', 'contentieux/pieces/ajouter', 'contentieux/pieces/version', 'contentieux/pieces/importer', 'contentieux/pieces/renumerotation',
                       'contentieux/actes/plan', 'contentieux/actes/rediger', 'contentieux/actes/section', 'contentieux/actes/controler', 'contentieux/actes/publier', 'contentieux/delais/candidat', 'contentieux/delais/corriger', 'contentieux/delais/ia',
                       'contentieux/audiences/preparer', 'contentieux/paquets/creer', 'contentieux/paquets/modifier', 'contentieux/paquets/verifier'))
# Actions de validation : avocat ou administrateur. Un assistant prépare ; il ne valide ni n'autorise.
VALIDATIONS = frozenset(('contentieux/matrice/decision', 'contentieux/pieces/retirer', 'contentieux/pieces/renumerotation/appliquer', 'contentieux/categories/decision', 'contentieux/actes/annuler', 'contentieux/delais/decision', 'contentieux/delais/regle', 'contentieux/paquets/autoriser'))
ONGLETS = (('matrice', 'Matrice'), ('pieces', 'Pièces'), ('actes', 'Actes'), ('delais', 'Délais'), ('audience', 'Audience'), ('paquets', 'Paquets'), ('categories', 'Classement'))
MESSAGES = {'role_insuffisant': 'Cette action est réservée à un avocat ou à un administrateur.', 'dossier_reserve': 'Ce dossier ne vous est pas accessible.', 'version_en_conflit': 'Cette page n’est plus à jour : rechargez-la avant de décider.', 'version_requise': 'Rechargez la page avant de décider.',
            'cle_idempotence_requise': 'Rechargez la page avant de renvoyer ce formulaire.', 'cle_idempotence_deja_utilisee': 'Ce formulaire a déjà servi : rechargez la page.', 'aucune_pretention_validee': 'Validez d’abord au moins une prétention du cabinet : un acte ne se construit pas sur des propositions.',
            'numero_deja_utilise': 'Ce numéro est déjà utilisé pour cette partie.', 'piece_recue_non_renumerotable': 'Une pièce reçue ne se renumérote pas : seules les pièces du cabinet le peuvent.', 'renumerotation_perimee': 'Une pièce a changé depuis la proposition : refaites-la.', 'empreinte_differente_du_registre': 'Le fichier ne correspond pas à l’empreinte annoncée.',
            'date_non_certaine_a_completer': 'Ce délai n’a pas de date certaine : complétez les données ou le calcul à la main.', 'regle_a_valider': 'Validez d’abord la règle pour son périmètre.', 'regle_modifiee_recalculer': 'La règle a changé depuis ce calcul : corrigez le candidat pour le recalculer.',
            'manifeste_modifie': 'Un fichier a changé depuis ce manifeste : l’autorisation n’est pas accordée.', 'autorisation_absente_ou_perimee': 'L’autorisation manque ou ne couvre plus ce manifeste : vérifiez puis autorisez de nouveau.', 'controles_bloquants': 'Des contrôles restent bloquants : levez-les avant d’autoriser.',
            'acte_non_publie': 'Publiez d’abord le projet d’acte.', 'acte_non_pret': 'L’acte n’a pas encore été rédigé et contrôlé.', 'destinataire_requis': 'Indiquez au moins un destinataire.', 'canal_invalide': 'Choisissez un canal.', 'paquet_vide': 'Ce paquet ne contient aucun fichier.', 'motif_obligatoire': 'Indiquez un motif.',
            'canal_non_integre': 'Aucun canal de dépôt n’est intégré à AxiorHub.', 'candidat_clos': 'Ce candidat a déjà été remplacé, rejeté ou validé : rechargez la page.'}
STATUS = {'role_insuffisant': '403 Forbidden', 'dossier_reserve': '403 Forbidden', 'version_en_conflit': '409 Conflict', 'version_requise': '409 Conflict', 'cle_idempotence_deja_utilisee': '409 Conflict', 'dossier_absent': '404 Not Found', 'route_inconnue': '404 Not Found'}


def allowed(role, route):
    route = route.split('?', 1)[0]
    if route in VALIDATIONS:
        return role in ('administrateur', 'avocat')
    return route in LECTURES or route in ECRITURES


def _erreur(error):
    from .erreurs5634 import explain
    from .web440 import json_out
    code = str(error).split(':')[0]
    return json_out({'error': code, 'message': MESSAGES.get(code) or explain(code, getattr(error, 'detail', None))}, STATUS.get(code, '400 Bad Request'))


def api(env, desk, auth, prefix, name, args, method):
    try:
        return _route(env, desk, auth, prefix, name, args, method)
    except Stop as error:
        return _erreur(error)


def _inputs(data):
    """Formulaire à plat : champs ``c_<condition>``. Une valeur vide reste inconnue."""
    out = dict(data.get('inputs')) if isinstance(data.get('inputs'), dict) else {}
    out.update({k[2:]: v for k, v in data.items() if k.startswith('c_') and str(v).strip()})
    return out


def _destinataires(data):
    if isinstance(data.get('destinataires'), list):
        return data['destinataires']
    return [{'nom': data.get('destinataire'), 'qualite': data.get('qualite'), 'adresse': data.get('adresse')}]


def _annexes(data):
    valeur = data.get('annexes')
    return valeur if isinstance(valeur, list) else [x.strip() for x in str(valeur or '').splitlines() if x.strip()]


def _agenda(desk, data):
    """Le client d'agenda n'est ouvert que si la personne demande l'inscription."""
    if not data.get('agenda'):
        return None
    from .dav import DAV
    return DAV(desk.c['nextcloud'])


def _route(env, desk, auth, prefix, name, args, method):
    from . import contentieux5689 as base, contentieux_actes5689 as actes, contentieux_paquets5689 as paquets, contentieux_pieces5689 as pieces, delais5689 as delais
    from .web440 import check_post, json_out
    from .web567 import actor
    owner, role = actor(env)
    route = name[len('m5689/'):].split('?', 1)[0]
    if not allowed(role, route):
        raise Stop('role_insuffisant' if route in VALIDATIONS else 'route_inconnue')
    base.schema(desk)
    delais.schema(desk)
    mid = str(args.get('dossier') or '')
    if method == 'GET':
        if route not in LECTURES:
            raise Stop('methode_non_autorisee')
        ident = str(args.get('id') or '')
        if route == 'contentieux/matrice':
            return json_out({'matrice': base.matrice(desk, mid), 'revision': base.revision(desk, mid)})
        if route == 'contentieux/paquet-contentieux':
            return json_out(base.litigation_packet(desk, mid))
        if route == 'contentieux/pieces':
            return json_out(pieces.liste(desk, mid, str(args.get('tous') or '') == '1'))
        if route == 'contentieux/doublons':
            return json_out({'doublons': pieces.doublons(desk, mid)})
        if route == 'contentieux/categories':
            return json_out({'categories': pieces.categories(desk, mid, str(args.get('statut') or ''))})
        if route == 'contentieux/actes':
            return json_out({'actes': actes.liste(desk, mid)})
        if route == 'contentieux/actes/vue':
            return json_out(actes.vue(desk, ident))
        if route == 'contentieux/delais':
            return json_out({'candidats': delais.liste(desk, mid), 'a_decider': delais.a_decider(desk, mid), 'alertes': delais.alertes(desk, mid)})
        if route == 'contentieux/regles':
            return json_out(delais.regles_publiques(desk))
        if route == 'contentieux/coherence':
            return json_out(delais.coherence(desk, mid))
        if route == 'contentieux/audiences':
            return json_out({'audiences': paquets.audiences(desk, mid)})
        if route == 'contentieux/audiences/vue':
            return json_out(paquets.vue_audience(desk, ident))
        if route == 'contentieux/paquets':
            return json_out({'paquets': paquets.liste(desk, mid)})
        if route == 'contentieux/paquets/vue':
            return json_out(paquets.vue(desk, ident))
        raw, nom = paquets.archive(desk, ident, owner)
        from .livrables_export5689 import _disposition
        return {'status': '200 OK', 'kind': 'application/zip', 'body': raw, 'headers': [('Content-Disposition', _disposition(nom))]}
    data = check_post(env, auth)
    mid = str(data.get('dossier') or '')
    ident = str(data.get('id') or '')
    if route == 'contentieux/matrice/pretention':
        return json_out(base.creer_pretention(desk, mid, data, owner, role))
    if route == 'contentieux/matrice/element':
        return json_out(base.creer_element(desk, mid, data, owner, role))
    if route == 'contentieux/matrice/decision':
        return json_out(base.decider(desk, mid, data, owner, role))
    if route == 'contentieux/matrice/amorcer':
        return json_out(base.amorcer(desk, mid, owner, role))
    if route == 'contentieux/matrice/ia':
        return json_out(base.demander_ia(desk, mid, owner, role))
    if route == 'contentieux/pieces/ajouter':
        return json_out(pieces.ajouter(desk, mid, data, owner, role))
    if route == 'contentieux/pieces/version':
        return json_out(pieces.nouvelle_version(desk, mid, ident, data, owner, role))
    if route == 'contentieux/pieces/retirer':
        return json_out(pieces.retirer(desk, mid, ident, data, owner, role))
    if route == 'contentieux/pieces/importer':
        return json_out(pieces.importer_registre(desk, mid, owner))
    if route == 'contentieux/pieces/renumerotation':
        return json_out(pieces.proposer_renumerotation(desk, mid, data, owner, role))
    if route == 'contentieux/pieces/renumerotation/appliquer':
        return json_out(pieces.appliquer_renumerotation(desk, mid, data, owner, role))
    if route == 'contentieux/categories/decision':
        return json_out(pieces.decider_categorie(desk, mid, data, owner, role))
    if route == 'contentieux/actes/plan':
        return json_out(actes.creer(desk, mid, data, owner, role))
    if route == 'contentieux/actes/rediger':
        return json_out(actes.lancer(desk, ident, owner, role))
    if route == 'contentieux/actes/section':
        return json_out(actes.modifier_section(desk, data, owner, role))
    if route == 'contentieux/actes/controler':
        return json_out(actes.controler(desk, ident, owner))
    if route == 'contentieux/actes/publier':
        return json_out(actes.publier(desk, ident, owner, role))
    if route == 'contentieux/actes/annuler':
        return json_out(actes.annuler(desk, ident, owner, role))
    if route == 'contentieux/delais/candidat':
        return json_out(delais.proposer(desk, mid, {**data, 'inputs': _inputs(data)}, owner, role))
    if route == 'contentieux/delais/corriger':
        return json_out(delais.corriger(desk, ident, {**data, 'inputs': _inputs(data)}, owner, role))
    if route == 'contentieux/delais/decision':
        return json_out(delais.decider(desk, ident, data, owner, role, _agenda(desk, data)))
    if route == 'contentieux/delais/regle':
        return json_out(delais.valider_regle(desk, data, owner, role))
    if route == 'contentieux/delais/ia':
        return json_out(delais.demander_ia(desk, mid, data.get('path'), owner, role))
    if route == 'contentieux/audiences/preparer':
        return json_out(paquets.preparer_audience(desk, mid, data, owner, role))
    if route == 'contentieux/paquets/creer':
        return json_out(paquets.creer(desk, mid, {**data, 'destinataires': _destinataires(data), 'annexes': _annexes(data)}, owner, role))
    if route == 'contentieux/paquets/modifier':
        change = dict(data)
        if data.get('destinataire') or isinstance(data.get('destinataires'), list):
            change['destinataires'] = _destinataires(data)
        if 'annexes' in data:
            change['annexes'] = _annexes(data)
        return json_out(paquets.modifier(desk, ident, change, owner, role))
    if route == 'contentieux/paquets/verifier':
        return json_out(paquets.verifier(desk, ident))
    if route == 'contentieux/paquets/autoriser':
        return json_out(paquets.autoriser(desk, ident, data, owner, role))
    raise Stop('route_inconnue')


# ------------------------------------------------------------------------------------------------ page
def _cle():
    return '<input type="hidden" name="cle" value="%s">' % uuid.uuid4().hex


def _cache(nom, valeur):
    return '<input type="hidden" name="%s" value="%s">' % (e(nom, quote=True), e(str(valeur), quote=True))


def _form(api_nom, champs, bouton, mid='', classe='m5-form', ghost=False):
    return '<form class="%s" data-api="m5689/contentieux/%s" data-reload>%s%s<button class="ax-btn%s" type="submit">%s</button><p role="status" class="m5-status"></p></form>' % (classe, e(api_nom, quote=True), _cache('dossier', mid) if mid else '', champs, ' ghost' if ghost else '', e(bouton))


def _champ(libelle, nom, valeur='', longueur=300):
    return '<label class="m5-field">%s<input name="%s" maxlength="%d" value="%s"></label>' % (e(libelle), e(nom, quote=True), longueur, e(str(valeur), quote=True))


def _zone(libelle, nom, valeur=''):
    return '<label class="m5-field">%s<textarea name="%s" rows="3">%s</textarea></label>' % (e(libelle), e(nom, quote=True), e(str(valeur)))


def _select(libelle, nom, choix, courant=''):
    return '<label class="m5-field">%s<select name="%s"><option value="">À préciser</option>%s</select></label>' % (e(libelle), e(nom, quote=True), ''.join('<option value="%s"%s>%s</option>' % (e(k, quote=True), ' selected' if k == courant else '', e(v)) for k, v in choix.items()))


def _coche(libelle, nom):
    return '<label class="m5-field"><span><input type="checkbox" name="%s" class="m5-check"> %s</span></label>' % (e(nom, quote=True), e(libelle))


def _lien_fichier(prefix, mid, source):
    path = source.get('path') or ''
    if not path:
        return ''
    from urllib.parse import urlencode
    ancre = '#page=' + str(source['locator']) if source.get('locator_kind') == 'pdf_page' and source.get('locator') else ''
    return ' <a href="%s" target="_blank" rel="noopener">Ouvrir le passage</a>' % e('%s/dossier/fichier?%s%s' % (prefix, urlencode({'matter': mid, 'path': path}), ancre), quote=True)


def _decision(mid, objet, ligne, peut):
    if not peut or ligne['statut'] != 'propose':
        return ''
    commun = _cache('objet', objet) + _cache('id', ligne['id']) + _cache('version', ligne['revision'])
    return _form('matrice/decision', commun + _cache('action', 'valider'), 'Valider', mid, 'm5-form m5-inline m5-mini') + _form('matrice/decision', commun + _cache('action', 'rejeter'), 'Rejeter', mid, 'm5-form m5-inline m5-mini', True)


def _section_matrice(desk, prefix, mid, peut):
    from . import contentieux5689 as base
    claims = base.matrice(desk, mid)
    html = ('<section class="ax-card"><h2>Prétentions</h2><p class="ax-muted">Un élément proposé par l’IA reste « proposé » tant qu’un avocat ne l’a pas validé. Valider enregistre la position du cabinet, pas la vérité d’une allégation.</p>'
            '<div class="ax-actions">%s%s</div></section>') % (_form('matrice/amorcer', '', 'Reprendre les demandes du dossier', mid, 'm5-form m5-inline', True), _form('matrice/ia', '', 'Demander des propositions à l’IA', mid, 'm5-form m5-inline', True))
    for c in claims:
        lignes = ''
        for kind, libelle in base.KINDS.items():
            for x in c['elements'][kind]:
                auteur = ('IA' if x['auteur_type'] == 'ia' else x['auteur']) + ((', selon ' + x['affirme_par']) if x['affirme_par'] else '')
                lignes += '<tr><td>%s</td><td>%s%s%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>' % (
                    e(libelle), e(x['texte']), (' <small>(%s)</small>' % e(x['citation'])) if x['citation'] else '', _lien_fichier(prefix, mid, x['source']), e(x['justification']) or '—', e(auteur), '<strong>%s</strong>' % e(x['etat_libelle']), e(x['statut_libelle']) + _decision(mid, 'element', x, peut))
        for m in c['manques']:
            lignes += '<tr><td>%s</td><td colspan="3" class="ax-muted">Aucun élément sourcé</td><td><strong>Manquant</strong></td><td>—</td></tr>' % e(m['libelle'])
        montant = (' · %s %s' % (e(c['montant']), e(c['devise']))) if c['montant'] else ''
        html += ('<section class="ax-card"><h3>%s</h3><p>%s%s · %s · %s %s</p><p class="ax-muted">Connu : %d · Contesté : %d · Manquant : %d</p>'
                 '<div class="table"><table><thead><tr><th>Élément</th><th>Texte</th><th>Justification</th><th>Auteur</th><th>État</th><th>Validation</th></tr></thead><tbody>%s</tbody></table></div>'
                 '<details><summary>Ajouter un élément</summary>%s</details></section>') % (
            e(c['titre']), e(c['demande']), montant, 'Cabinet' if c['cote'] == 'cabinet' else 'Partie adverse', e(c['statut_libelle']), _decision(mid, 'pretention', c, peut), c['bilan']['connu'], c['bilan']['conteste'], c['bilan']['manquant'], lignes,
            _form('matrice/element', _cle() + _cache('claim_id', c['id']) + _select('Nature', 'kind', base.KINDS) + _zone('Texte', 'texte') + _zone('Justification du lien', 'justification') + _champ('Affirmé par', 'affirme_par', '', 200) + _coche('Source vérifiée (règle)', 'source_verifiee') + (_coche('Valider cet élément', 'valider') if peut else ''), 'Ajouter', mid))
    html += '<section class="ax-card"><h3>Nouvelle prétention</h3>%s</section>' % _form('matrice/pretention', _cle() + _champ('Intitulé', 'titre') + _zone('Demande', 'demande') + _select('Côté', 'cote', {'cabinet': 'Cabinet', 'adverse': 'Partie adverse'}, 'cabinet') + _champ('Montant', 'montant', '', 30) + (_coche('Valider cette prétention', 'valider') if peut else ''), 'Ajouter', mid)
    return html


def _section_pieces(desk, prefix, mid, peut):
    from . import contentieux_pieces5689 as pieces
    registre = pieces.liste(desk, mid)
    html = '<section class="ax-card"><h2>Pièces par partie</h2><p class="ax-muted">Le numéro reste celui qui figure sur la pièce. Le n° 1 du cabinet et le n° 1 adverse sont deux pièces. Rien n’est renommé ni réécrit.</p><div class="ax-actions">%s</div>' % _form('pieces/importer', '', 'Reprendre le classement du dossier', mid, 'm5-form m5-inline', True)
    for g in registre['groupes']:
        lignes = ''.join('<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>' % (e(p['numero']), e(p['titre']), e({'present': 'Fichier présent', 'version_changee': 'Fichier changé', 'missing': 'Fichier absent', 'unreadable': 'Illisible'}.get(p['etat_fichier'], 'Non vérifié')), 'À confirmer' if p['a_confirmer'] else ('Reçue' if p['recue'] else '—')) for p in g['pieces'])
        html += '<h3>%s</h3><div class="table"><table><thead><tr><th>N°</th><th>Titre</th><th>Fichier</th><th>Note</th></tr></thead><tbody>%s</tbody></table></div>' % (e(g['libelle']), lignes)
    if not registre['groupes']:
        html += '<p class="ax-muted">Aucune pièce enregistrée.</p>'
    doublons = pieces.doublons(desk, mid)
    if doublons:
        html += '<h3>Même contenu</h3><ul>%s</ul>' % ''.join('<li>%s : %s</li>' % (e(d['message']), e(', '.join(p['citation'] for p in d['pieces']))) for d in doublons)
    html += '<details><summary>Ajouter une pièce</summary>%s</details></section>' % _form('pieces/ajouter', _cle() + _select('Côté', 'cote', pieces.COTES) + _champ('Partie productrice', 'partie', '', 200) + _champ('Numéro affiché', 'numero', '', 30) + _champ('Titre', 'titre') + _champ('Chemin du fichier', 'path', '', 800) + _champ('Provenance', 'provenance'), 'Ajouter', mid)
    return html


def _section_actes(desk, prefix, mid, peut):
    from . import contentieux_actes5689 as actes
    html = '<section class="ax-card"><h2>Actes</h2><p class="ax-muted">Le plan vient des prétentions validées. « Prêt à relire » dit que les contrôles ont tourné, pas que l’acte est validé.</p>%s</section>' % _form('actes/plan', _cle() + _select('Acte', 'type_acte', actes.TYPES, 'conclusions') + _champ('Titre', 'titre', '', 200) + _champ('Juridiction', 'juridiction', '', 200), 'Préparer le plan', mid)
    for a in actes.liste(desk, mid):
        d = actes.vue(desk, a['id'])
        sections = ''.join('<li>%s : %s%s%s</li>' % (e(s['titre']), e(s['etat_libelle']), ' (à reprendre : les données ont changé)' if s['a_reprendre'] else '', (' : ' + e(s['erreur'])) if s['erreur'] else '') for s in d['sections'])
        controles = ''.join('<li>%s : %s%s</li>' % (e(c['libelle']), {'ok': 'Conforme', 'reserve': 'Réserve', 'bloquant': 'Bloquant'}[c['etat']], ''.join('<br><small>%s</small>' % e(x) for x in c['bloquants'] + c['reserves'])) for c in d['controles'].get('controles', []))
        html += ('<section class="ax-card"><h3>%s</h3><p>%s%s</p><ol>%s</ol>%s<div class="ax-actions">%s%s%s%s</div></section>') % (
            e(d['titre']), e(d['etat_libelle']), '' if d['controles_a_jour'] or not d['controles'] else ' · contrôles à refaire', sections, ('<h4>Contrôles</h4><ul>%s</ul>' % controles) if controles else '',
            _form('actes/rediger', _cache('id', d['id']), 'Rédiger ou reprendre', '', 'm5-form m5-inline'), _form('actes/controler', _cache('id', d['id']), 'Contrôler', '', 'm5-form m5-inline', True), _form('actes/publier', _cache('id', d['id']), 'Publier le projet', '', 'm5-form m5-inline', True),
            _form('actes/annuler', _cache('id', d['id']), 'Annuler', '', 'm5-form m5-inline', True) if peut else '')
    return html


def _champs_conditions(rule_id, inputs=None):
    from . import delais5689 as delais
    data, inputs = delais.ressource(), inputs or {}
    r = delais.regle(rule_id)
    html = ''
    for ident in (r['conditions'] if r else ()):
        c = data['conditions'][ident]
        html += _champ(c['question'], 'c_' + ident, inputs.get(ident, ''), 10) if c.get('type') == 'date' else _select(c['question'], 'c_' + ident, c['choix'], inputs.get(ident, ''))
    return html


def _section_delais(desk, prefix, mid, peut):
    from . import delais5689 as delais
    regles = delais.regles_publiques(desk)
    html = '<section class="ax-card"><h2>Délais</h2><p class="ax-muted">%s</p>' % e(regles['avertissement'])
    for a in delais.alertes(desk, mid):
        html += '<p role="status"><strong>%s</strong></p>' % e(a['message'])
    html += _form('delais/candidat', _cle() + _select('Événement', 'evenement', delais.ressource()['evenements']) + _champ('Date de l’événement (AAAA-MM-JJ)', 'date_evenement', '', 10) + _champ('Nature de la procédure', 'nature_procedure', '', 120) + _champ('Juridiction', 'juridiction', '', 200), 'Ajouter un délai à calculer', mid) + '</section>'
    for x in delais.liste(desk, mid):
        date_txt = '<strong>À vérifier : aucune date certaine</strong>' if x['statut'] == 'a_verifier' else '%s (%s)' % (e(x.get('date_libelle', '')), e(x['statut_libelle']))
        questions = ''.join('<li>%s</li>' % e(m['question']) for m in x['manquants']) + ''.join('<li>%s</li>' % e(m) for m in x['calcul'].get('motifs', []))
        etapes = ''.join('<li>%s</li>' % e(s) for s in x['calcul'].get('etapes', []))
        actions = ''
        if x['statut'] in ('a_verifier', 'propose'):
            actions += '<details><summary>Corriger ou compléter</summary>%s</details>' % _form('delais/corriger', _cle() + _cache('id', x['id']) + _cache('version', x['revision']) + _champ('Date de l’événement', 'date_evenement', x['date_evenement'], 10) + _champs_conditions(x['rule_id'], x['inputs']), 'Recalculer')
        if peut and x['statut'] == 'propose':
            actions += _form('delais/decision', _cache('id', x['id']) + _cache('version', x['revision']) + _cache('action', 'valider'), 'Valider la date', '', 'm5-form m5-inline')
        if peut and x['statut'] in ('propose', 'a_verifier'):
            # 5.6.96 (D03) : écarter une fausse détection, motif obligatoire ; même route que la validation (action « rejeter »),
            # droits, CSRF et version vérifiés par le serveur (409 si la page est périmée). Aucun agenda n'est touché.
            actions += '<details><summary>Écarter la proposition</summary>%s</details>' % _form(
                'delais/decision', _cle() + _cache('id', x['id']) + _cache('version', x['revision']) + _cache('action', 'rejeter') + _champ_motif(x['id']),
                'Écarter la proposition', mid, 'm5-form', True)
        html += '<section class="ax-card"><h3>%s</h3><p>%s</p>%s%s%s%s</section>' % (e(delais.ressource()['evenements'].get(x['evenement'], x['evenement'] or 'Événement à préciser')), date_txt, ('<p>Citation : %s</p>' % e(x['citation'])) if x['citation'] else '',
                                                                                  ('<h4>Données à fournir</h4><ul>%s</ul>' % questions) if questions else '', ('<details><summary>Étapes du calcul</summary><ol>%s</ol></details>' % etapes) if etapes else '', actions)
    lignes = ''.join('<li><strong>%s</strong> (version %d) : %s<br><small>%s</small>%s</li>' % (e(r['libelle']), r['version'], e(r['perimetre']), e(', '.join(s['reference'] for s in r['sources'])), ' Validée.' if r['validee'] else (_form('delais/regle', _cache('rule_id', r['id']) + _cache('empreinte', delais.empreinte_regle(delais.regle(r['id']))), 'Valider cette règle pour ce périmètre', '', 'm5-form m5-inline m5-mini', True) if peut else ' À valider par un avocat.')) for r in regles['regles'])
    coherence = delais.coherence(desk, mid)
    suivi = ''.join('<li>%s : suivi %s, agenda en ligne : %s%s</li>' % (e(l['libelle']), e(l['date_suivi'] or 'sans date'), e(l['agenda_en_ligne']), ''.join('<br><small>%s</small>' % e(x) for x in l['ecarts'])) for l in coherence['lignes'])
    html += '<section class="ax-card"><h3>Règles disponibles</h3><p class="ax-muted">%s</p><ul>%s</ul></section><section class="ax-card"><h3>Suivi de procédure et agendas</h3><p class="ax-muted">%s</p><ul>%s</ul></section>' % (e(regles['perimetre_commun']), lignes, e(coherence['limite']), suivi or '<li class="ax-muted">Aucune échéance suivie.</li>')
    return html + _actes_consignes(desk, mid)


def _champ_motif(ident):
    """5.6.96 (D03) : motif obligatoire (3 caractères au moins), motifs suggérés proposés par une liste, consigne liée au champ."""
    from .echeances450 import MOTIFS_ECART
    cle = 'm5-motif-' + str(ident)
    return ('<label class="m5-field">Motif (obligatoire)<input name="motif" required minlength="3" maxlength="500" list="%s-liste" '
            'aria-describedby="%s-aide" autocomplete="off"></label><datalist id="%s-liste">%s</datalist>'
            '<p class="ax-muted" id="%s-aide">Par exemple : arrêt cité, acte d’incident, notification non prouvée, doublon, ou votre propre motif.</p>') % (
        e(cle, quote=True), e(cle, quote=True), e(cle, quote=True), ''.join('<option value="%s"></option>' % e(m, quote=True) for m in MOTIFS_ECART), e(cle, quote=True))


def _actes_consignes(desk, mid):
    """5.6.96 (D01) : actes qualifiés sans délai calculé (acte d'incident, calendrier), avec leur source et leur mention."""
    from .echeances450 import actes_consignes
    lignes = ''.join('<li><strong>%s</strong> : %s<br><small>Source : %s</small>%s</li>' % (
        e(a['type_libelle']), e(a['mention']), e(a['path'].rsplit('/', 1)[-1]),
        ''.join('<br><small>Date à vérifier : %s (« %s »)</small>' % (e(d['date']), e(d['extrait'])) for d in a['qualification'].get('dates_expresses', [])))
        for a in actes_consignes(desk, mid, 10) if a['mention'])
    return '<section class="ax-card"><h3>Actes qualifiés</h3><ul>%s</ul></section>' % lignes if lignes else ''


def _section_audience(desk, prefix, mid, peut):
    from . import contentieux_paquets5689 as paquets
    html = '<section class="ax-card"><h2>Dossier d’audience</h2><p class="ax-muted">Figé à sa préparation : une modification ultérieure est signalée, jamais reportée. Les notes internes y restent marquées et n’entrent dans aucun paquet.</p>%s</section>' % _form('audiences/preparer', _cle(), 'Préparer une version figée', mid)
    for a in paquets.audiences(desk, mid):
        v = paquets.vue_audience(desk, a['id'])
        c = v['contenu']
        html += '<section class="ax-card"><h3>Version %d, préparée le %s</h3>%s<ul><li>%d événement(s)</li><li>%d demande(s)</li><li>%d point(s) discuté(s)</li><li>%d objection(s)</li><li>%d pièce(s) à ouvrir</li><li>%d note(s) interne(s), usage interne</li></ul></section>' % (
            v['version'], e(v['prepare_le'][:10]), ('<p role="status"><strong>Modifié depuis : %s</strong></p>' % e(', '.join(v['parties_modifiees']))) if v['modifie_depuis'] else '<p role="status">Aucune modification depuis la préparation.</p>', len(c['chronologie']), len(c['demandes']), len(c['points_discutes']), len(c['objections']), len(c['pieces_a_ouvrir']), len(c['notes_internes']))
    return html


def _section_paquets(desk, prefix, mid, peut):
    from . import contentieux_actes5689 as actes, contentieux_paquets5689 as paquets
    publies = {a['id']: a['titre'] for a in actes.liste(desk, mid) if a.get('path')}
    html = ('<section class="ax-card"><h2>Paquets à communiquer</h2><p class="ax-muted">AxiorHub ne dépose rien : aucune interface officielle de dépôt n’est intégrée. Une autorisation porte sur le manifeste affiché ; toute modification l’invalide. %s</p>%s</section>') % (
        e(paquets.MENTION), _form('paquets/creer', _cle() + _select('Acte publié', 'acte_id', publies) + _champ('Destinataire', 'destinataire', '', 200) + _champ('Qualité', 'qualite', '', 100) + _select('Canal', 'canal', paquets.CANAUX) + _zone('Annexes (un chemin par ligne)', 'annexes'), 'Préparer le manifeste', mid))
    for p in paquets.liste(desk, mid):
        v = paquets.vue(desk, p['id'])
        fichiers = ''.join('<tr><td>%s</td><td>%s</td><td>%s</td><td><code>%s</code></td><td>%s</td></tr>' % (e(f['role']), e(f['titre']), e(f['version_id']), e(f['sha256'][:16]), _lien_fichier(prefix, mid, f)) for f in v['fichiers'])
        exclus = ''.join('<li>%s : %s</li>' % (e(x['titre']), e(x['motif'])) for x in v['exclus'])
        controles = ''.join('<li>%s : %s %s</li>' % (e(c['libelle']), {'ok': 'Conforme', 'reserve': 'Réserve', 'bloquant': 'Bloquant'}[c['etat']], e(c['detail'])) for c in v['controles'].get('lignes', []))
        ecarts = ''.join('<p role="status"><strong>%s</strong></p>' % e(x) for x in v['ecarts_locaux'])
        actions = _form('paquets/verifier', _cache('id', v['id']), 'Revérifier les fichiers', '', 'm5-form m5-inline', True)
        if peut:
            actions += _form('paquets/autoriser', _cache('id', v['id']) + _cache('manifeste_sha', v['manifeste_sha']) + _select('Action autorisée', 'action', paquets.ACTIONS), 'Autoriser ce manifeste', '', 'm5-form m5-inline')
        if v['etat'] == 'autorise':
            actions += '<a class="ax-btn ghost" href="%s">Télécharger l’archive</a>' % e('%s/api440/m5689/contentieux/paquets/export?id=%s' % (prefix, v['id']), quote=True)
        html += ('<section class="ax-card"><h3>Paquet, version %d</h3><p>%s · %s · %s</p>%s<div class="table"><table><thead><tr><th>Rôle</th><th>Fichier</th><th>Version</th><th>Empreinte</th><th></th></tr></thead><tbody>%s</tbody></table></div><ul>%s</ul>%s<p class="ax-muted">Empreinte du manifeste : <code>%s</code></p><div class="ax-actions">%s</div></section>') % (
            v['version'], e(v['etat_libelle']), e(v['canal_libelle']), e(', '.join(d['nom'] for d in v['destinataires'])), ecarts, fichiers, controles, ('<h4>Exclus du paquet</h4><ul>%s</ul>' % exclus) if exclus else '', e(v['manifeste_sha']), actions)
    return html


def _section_categories(desk, prefix, mid, peut):
    from . import contentieux_pieces5689 as pieces
    pieces.migrer_categories(desk, mid)
    lignes = ''
    for c in pieces.categories(desk, mid):
        action = ''
        if peut and c['statut'] == 'propose':
            action = _form('categories/decision', _cache('path', c['path']) + _cache('version', c['revision']) + _select('Catégorie', 'categorie', {k: v for k, v in pieces.CATEGORIES.items() if k != 'a_reclasser'}, c['categorie']), 'Valider', mid, 'm5-form m5-inline m5-mini')
        ancien = c['classement_anterieur']
        lignes += '<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>' % (e(c['nom']), e(c['categorie_libelle']), e(ancien.get('nature', '')), 'Validée' if c['statut'] == 'valide' else 'Proposée', action)
    return '<section class="ax-card"><h2>Classement des documents</h2><p class="ax-muted">Proposition à valider : le classement antérieur reste conservé. Le dossier Pièces n’est pas le répertoire de tous les fichiers.</p><div class="table"><table><thead><tr><th>Document</th><th>Catégorie</th><th>Classement antérieur</th><th>État</th><th></th></tr></thead><tbody>%s</tbody></table></div></section>' % (lignes or '<tr><td colspan="5" class="ax-muted">Aucun document relevé.</td></tr>')


def page(desk, env, auth, prefix, args, shell):
    from urllib.parse import urlencode
    from . import contentieux5689 as base, delais5689 as delais
    from .common import matter_display
    from .web567 import actor
    owner, role = actor(env)
    mid = str(args.get('dossier') or '')
    if not mid:
        from .common import load_matters
        options = ''.join('<option value="%s">%s</option>' % (e(m['id'], quote=True), e(matter_display(m))) for m in sorted(load_matters(desk.c), key=lambda m: matter_display(m).casefold()))
        return shell('Contentieux', '<h1>Contentieux</h1><p class="ax-muted">Le contentieux se prépare dossier par dossier.</p><form method="get"><label class="m5-field">Dossier<select name="dossier">%s</select></label><button class="ax-btn" type="submit">Ouvrir</button></form>' % options, prefix, auth['csrf'], '/dossiers')
    dossier = base.garde(desk, mid)
    base.schema(desk)
    delais.schema(desk)
    onglet = str(args.get('onglet') or 'matrice')
    if onglet not in dict(ONGLETS):
        onglet = 'matrice'
    peut = role in base.VALIDEURS
    nav = ''.join('<a class="ax-btn%s" href="%s"%s>%s</a> ' % ('' if k == onglet else ' ghost', e(prefix + '/contentieux?' + urlencode({'dossier': mid, 'onglet': k}), quote=True), ' aria-current="page"' if k == onglet else '', e(v)) for k, v in ONGLETS)
    corps = {'matrice': _section_matrice, 'pieces': _section_pieces, 'actes': _section_actes, 'delais': _section_delais, 'audience': _section_audience, 'paquets': _section_paquets, 'categories': _section_categories}[onglet](desk, prefix, mid, peut)
    from .ui_navigation5689 import libelle_source, valider_retour     # 5.6.89 (§ 14) : retour commun, contexte validé, repli sur le dossier
    cible = valider_retour(args.get('return_to') or '', prefix) or prefix + '/dossier?' + urlencode({'id': mid})
    retour = '<p><a class="ax-btn ghost ui89-retour" href="%s" data-ui89-retour>%s</a></p>' % (e(cible, quote=True), e(libelle_source(cible, prefix)))
    return shell('Contentieux', '<h1>Contentieux : %s</h1>%s<nav aria-label="Vues du contentieux">%s</nav>%s' % (e(matter_display(dossier)), retour, nav, corps), prefix, auth['csrf'], '/dossiers')


def page_route(env, cfg, auth, prefix, path, args, shell):
    """Page authentifiée : le contrôle d'accès du dossier est refait ici, avant tout rendu."""
    from .desk import Desk
    from .web440 import page_out
    desk = Desk(cfg)
    try:
        try:
            return page_out(page(desk, env, auth, prefix, args, shell))
        except Stop as ex:
            code = str(ex).split(':')[0]
            from .erreurs5634 import explain
            html = shell('Contentieux', '<p class="notice">%s</p><p><a class="ax-btn ghost" href="%s">Retour aux dossiers</a></p>' % (e(MESSAGES.get(code) or explain(code, getattr(ex, 'detail', None))), e(prefix + '/dossiers', quote=True)), prefix, auth['csrf'], '/dossiers')
            out = page_out(html)
            out['status'] = STATUS.get(code, '400 Bad Request')
            return out
    finally:
        try:
            desk.db.close()
        except Exception:   # noqa: BLE001
            pass
