"""5.6.89 : routes ``/api440/m5689/cabinet/*`` et ``/api440/m5689/connexions/*``, et l'assistant de démarrage de Paramètres.

L'acteur et son rôle viennent de la session serveur, jamais du corps de la requête ; chaque service refait son contrôle de droits. Toute écriture passe par ``check_post`` ;
une révision périmée rend 409, une modification hors droits rend 403. L'écran emploie les classes existantes (``ax-card``, ``ax-btn``, ``m5-form``) : le lot Carbon l'habillera.
"""
from html import escape as e
import re
from urllib.parse import quote
import uuid

from . import cabinet_profile5689 as profil
from . import connector_readiness5689 as prep
from .common import Stop

LECTURE = ('cabinet/profil', 'cabinet/preparation', 'cabinet/assistant', 'cabinet/manifeste', 'cabinet/versions', 'cabinet/demonstration', 'cabinet/demonstration/fichier', 'connexions/liste')
REGLAGES = ('cabinet/profil', 'cabinet/assistant', 'cabinet/alias', 'cabinet/proposition', 'cabinet/signature/deduire')
ECRITURE_CONNEXION = re.compile(r'connexions/([A-Za-z0-9_-]{1,80})/(qualifier|revoquer|reautoriser|nettoyer)')
LECTURE_CONNEXION = re.compile(r'connexions/([A-Za-z0-9_-]{1,80})/(preuves)')
ROLES = ('administrateur', 'avocat', 'assistant')
MESSAGES = {'revision_perimee': 'Ces informations ont changé depuis leur ouverture : rechargez la page, puis recommencez.', 'revision_requise': 'La révision lue est nécessaire pour enregistrer.',
            'secret_refuse_dans_le_profil': 'Le profil ne contient aucun secret. Les mots de passe, clés et jetons se saisissent dans Connexions, où ils vont au coffre.',
            'champ_profil_inconnu': 'Ce champ ne fait pas partie du profil du cabinet.', 'adresse_complete_requise': 'Indiquez une adresse complète : un domaine entier n’est pas accepté.',
            'adresse_expedition_non_retirable': 'L’adresse d’expédition ne peut pas être retirée : changez-la d’abord dans Connexions.', 'cle_idempotence_requise': 'Cette action demande une clé d’essai valide.',
            'cle_idempotence_deja_utilisee': 'Cette clé a déjà servi à une autre action.', 'capacite_revoquee': 'Cette capacité a été révoquée : aucun effet n’a eu lieu. Réautorisez-la pour la requalifier.',
            'ecriture_refusee_par_le_compte': 'Le compte relié refuse cette écriture : aucun effet n’a eu lieu.', 'test_interdit_envoi_ou_engagement': 'Cette capacité ne se teste pas : un essai enverrait un message ou créerait un engagement. Chaque opération reste validée par une personne.',
            'sonde_lecture_indisponible': 'AxiorHub ne sait pas encore essayer cette lecture sur ce service.', 'politique_refuse': 'La politique du cabinet refuse cet essai.', 'mission_inconnue': 'Ce type de mission n’existe pas.',
            'demonstration_requise': 'Lancez d’abord la mission de démonstration.', 'qualification_en_cours': 'Une qualification de cette capacité est déjà en cours.', 'proposition_deja_decidee': 'Cette proposition a déjà été traitée.',
            'capacite_inconnue': 'Cette connexion ne propose pas cette capacité.', 'etape_inconnue': 'Cette étape n’existe pas.', 'role_insuffisant': 'Votre compte n’a pas le droit de modifier cet élément.'}
CONFLITS = ('revision_perimee', 'version_en_conflit', 'cle_idempotence_deja_utilisee', 'qualification_en_cours', 'proposition_deja_decidee')
INTERDITS = ('role_insuffisant', 'dossier_reserve', 'connexions_personnelles_desactivees', 'politique_refuse', 'capacite_revoquee', 'test_interdit_envoi_ou_engagement')


def _route_connexion(route):
    return ECRITURE_CONNEXION.fullmatch(route) or LECTURE_CONNEXION.fullmatch(route)


def allowed(role, route):
    """Droits par route, en plus du droit fin ``reglages`` déclaré dans ``membres5650.category``. Une route inconnue est refusée."""
    route = route.split('?', 1)[0]
    if role not in ROLES:
        return False
    if route in LECTURE or route == 'cabinet/demonstration':
        return True
    if route in REGLAGES:
        return True                     # le rôle seul ne suffit pas : le service exige le droit « réglages »
    if ECRITURE_CONNEXION.fullmatch(route):
        return role in ('administrateur', 'avocat')
    return bool(LECTURE_CONNEXION.fullmatch(route))


def _erreur(error):
    from .erreurs5634 import explain
    from .web440 import json_out
    code = str(error).split(':')[0]
    message = MESSAGES.get(code)
    if not message:
        from .web import REASONS
        message = REASONS.get(code) or explain(code, getattr(error, 'detail', None))
    status = ('403 Forbidden' if code in INTERDITS else '409 Conflict' if code in CONFLITS else '404 Not Found' if code.endswith(('_absent', '_absente')) else '400 Bad Request')
    return json_out({'error': code, 'message': message}, status)


def api(env, desk, auth, prefix, name, args, method):
    from .web440 import check_post, json_out
    from .web567 import actor
    owner, role = actor(env)
    route = name[len('m5689/'):].split('?', 1)[0]
    try:
        if not allowed(role, route):
            raise Stop('role_insuffisant')
        if method in ('GET', 'HEAD'):
            return _lecture(desk, owner, role, route, args, prefix)
        if method != 'POST':
            raise Stop('methode_refusee')
        data = check_post(env, auth)
        return json_out(_ecriture(desk, owner, role, route, data))
    except Stop as error:
        return _erreur(error)


def _lecture(desk, owner, role, route, args, prefix):
    from .web440 import json_out
    if route == 'cabinet/profil':
        return json_out(profil.lire(desk))
    if route == 'cabinet/versions':
        return json_out({'versions': profil.versions(desk)})
    if route == 'cabinet/preparation':
        return json_out(prep.preparation(desk, owner, role, args.get('mission', '')))
    if route == 'cabinet/assistant':
        return json_out({**profil.assistant(desk), 'connexions': prep.tableau(desk, owner, role)})
    if route == 'cabinet/manifeste':
        return json_out(profil.manifeste(desk))
    if route == 'cabinet/demonstration':
        return json_out(profil.demonstration(desk, owner, args.get('id', '')))
    if route == 'cabinet/demonstration/fichier':
        raw, nom = profil.demonstration_docx(desk, owner, args.get('id', ''))
        return {'status': '200 OK', 'kind': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document', 'body': raw,
                'headers': [('Content-Disposition', "attachment; filename*=UTF-8''" + quote(nom))], 'csp': "default-src 'none'; sandbox"}
    if route == 'connexions/liste':
        return json_out({'connexions': prep.tableau(desk, owner, role)})
    match = LECTURE_CONNEXION.fullmatch(route)
    if match:
        conn = prep.connexion(desk, match[1])
        prep._peut_regler(desk, conn, owner, role)
        return json_out({'connexion': conn['id'], 'capacites': {c: prep.etat_capacite(desk, conn, c, owner, role) for c in conn['spec_caps']}, 'essais': prep.essais(desk, conn['id'])})
    raise Stop('route_inconnue')


def _vrai(value):
    return value in (True, 'true', 'on', '1', 1)


def _ecriture(desk, owner, role, route, data):
    if route == 'cabinet/profil':
        return profil.enregistrer(desk, owner, role, data)
    if route == 'cabinet/assistant':
        return profil.avancer(desk, owner, role, data)
    if route == 'cabinet/alias':
        return profil.enregistrer_alias(desk, owner, role, data.get('adresse'), str(data.get('statut') or 'confirmee'), str(data.get('compte') or 'messagerie'), str(data.get('role_personne') or ''))
    if route == 'cabinet/proposition':
        return profil.decider_proposition(desk, owner, role, data.get('id'), str(data.get('decision') or ''))
    if route == 'cabinet/signature/deduire':
        return profil.deduire(desk, owner, role, data.get('texte'), 'signature')
    if route == 'cabinet/demonstration':
        return profil.lancer_demonstration(desk, owner, role, data.get('cle'), str(data.get('langue') or ''))
    match = ECRITURE_CONNEXION.fullmatch(route)
    if not match:
        raise Stop('route_inconnue')
    conn_id, verbe = match[1], match[2]
    if verbe == 'qualifier':
        return prep.qualifier(desk, owner, role, conn_id, data.get('capacite'), data.get('cle'), _vrai(data.get('autoriser_creation')))
    if verbe == 'revoquer':
        return prep.revoquer(desk, owner, role, conn_id, str(data.get('capacite') or ''), data.get('cle'), str(data.get('motif') or ''))
    if verbe == 'reautoriser':
        return prep.reautoriser(desk, owner, role, conn_id, str(data.get('capacite') or ''), data.get('cle'))
    return prep.nettoyer(desk, owner, role, conn_id, data.get('test_id'))


# ------------------------------------------------------------------------------------------------ écran de Paramètres
def _cle():
    """Clé d'idempotence d'un bouton : stable pour la page affichée, renouvelée à chaque nouvel affichage."""
    return 'ui-' + uuid.uuid4().hex


def _champ(label, name, value, aide='', maxi=200, kind='text'):
    return ('<label>%s<input name="%s" type="%s" value="%s" maxlength="%d">%s</label>' % (
        e(label), e(name, quote=True), e(kind, quote=True), e(str(value or ''), quote=True), maxi, ('<small class="ax-muted">%s</small>' % e(aide)) if aide else ''))


def _zone(label, name, value, aide=''):
    return ('<label>%s<textarea name="%s" rows="4" maxlength="4000">%s</textarea>%s</label>' % (
        e(label), e(name, quote=True), e(str(value or '')), ('<small class="ax-muted">%s</small>' % e(aide)) if aide else ''))


def _boutons(etape, revision, suivant=True, retour=True):
    out = '<input type="hidden" name="etape" value="%s"><input type="hidden" name="revision" value="%d"><div class="c5689-actions">' % (e(etape, quote=True), revision)
    if suivant:
        out += '<button class="ax-btn" type="submit" data-extra="action=continuer">Enregistrer et continuer</button>'
    out += '<button class="ax-btn ghost" type="submit" data-extra="action=enregistrer">Enregistrer et reprendre plus tard</button>'
    if retour:
        out += '<button class="ax-btn ghost" type="submit" data-extra="action=retour">Étape précédente</button>'
    return out + '</div><p role="status" class="m5-status"></p>'


def _modes(data, valeur):
    choix = ''
    for key, mode in profil.DEPLOIEMENTS.items():
        etat = {'fourni': 'Fourni', 'partiel': 'Partiel', 'non_fourni': 'Non fourni par cette version'}[mode['etat']]
        choix += ('<label class="c5689-mode"><input type="radio" name="profil_deploiement" value="%s"%s><span><strong>%s</strong> <span class="c5689-badge c5689-%s">%s</span><br>'
                  '<span class="ax-muted">%s</span></span></label>') % (e(key, quote=True), ' checked' if valeur == key else '', e(mode['libelle']), e(mode['etat']), e(etat), e(mode['description']))
    return '<fieldset class="c5689-modes"><legend>Mode d’installation</legend>%s</fieldset>' % choix


def _alias_html(desk):
    lignes = ''
    for a in profil.aliases(desk):
        actions = ''
        for statut, libelle in (('confirmee', 'Confirmer'), ('retiree', 'Retirer')):
            if a['statut'] != statut:
                actions += ('<form class="m5-form c5689-inline" data-api="m5689/cabinet/alias" data-reload><input type="hidden" name="adresse" value="%s"><input type="hidden" name="compte" value="%s">'
                            '<input type="hidden" name="statut" value="%s"><input type="hidden" name="role_personne" value="%s"><button class="ax-btn ghost" type="submit">%s</button></form>') % (
                    e(a['adresse'], quote=True), e(a['compte'], quote=True), statut, e(a['role_personne'], quote=True), e(libelle))
        lignes += '<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>' % (e(a['adresse']), e(a['compte']), e(a['statut_libelle']), actions)
    if not lignes:
        return '<p class="ax-muted">Aucune adresse enregistrée pour l’instant.</p>'
    return '<div class="c5689-table"><table><thead><tr><th>Adresse</th><th>Compte</th><th>État</th><th>Action</th></tr></thead><tbody>%s</tbody></table></div>' % lignes


def _propositions_html(desk):
    rows = profil.propositions(desk)
    if not rows:
        return ''
    lignes = ''
    for p in rows:
        boutons = ''.join(('<form class="m5-form c5689-inline" data-api="m5689/cabinet/proposition" data-reload><input type="hidden" name="id" value="%s"><input type="hidden" name="decision" value="%s">'
                           '<button class="ax-btn ghost" type="submit">%s</button></form>') % (e(p['id'], quote=True), d, l) for d, l in (('confirmer', 'Confirmer'), ('rejeter', 'Écarter')))
        lignes += '<li><strong>%s :</strong> %s <span class="ax-muted">(déduit de : %s)</span> %s</li>' % (e(p['champ_libelle']), e(p['valeur']), e(p['origine']), boutons)
    return '<h4>Propositions à confirmer</h4><p class="ax-muted">Ces valeurs ont été déduites d’un texte : rien n’est appliqué sans votre accord.</p><ul class="c5689-liste">%s</ul>' % lignes


def _etape_identite(desk, etat):
    lu = profil.lire(desk)
    v = {**lu['identite'], **lu['coordonnees'], 'langue': lu['language'], 'activites': ', '.join(lu['practice_areas']), **etat['valeurs'].get('identite', {})}
    from .i18n5631 import LANGS
    langues = ''.join('<option value="%s"%s>%s</option>' % (l, ' selected' if v.get('langue') == l else '', {'fr': 'Français', 'en': 'Anglais', 'de': 'Allemand', 'es': 'Espagnol'}[l]) for l in LANGS)
    return ('<form class="m5-form c5689-form" data-api="m5689/cabinet/assistant" data-reload>%s%s%s%s%s<label>Langue de rédaction<select name="langue">%s</select></label>%s%s%s%s%s</form>'
            '<h4>Adresses reconnues</h4><p class="ax-muted">Un domaine entier n’est jamais assimilé au cabinet : seules ces adresses exactes le sont.</p>%s%s') % (
        _champ('Nom du cabinet', 'nom_cabinet', v.get('nom_cabinet')), _champ('Barreau', 'barreau', v.get('barreau'), maxi=120), _champ('Toque', 'toque', v.get('toque'), maxi=40),
        _champ('Adresse postale', 'adresse', v.get('adresse'), maxi=300), _champ('Téléphone', 'telephone', v.get('telephone'), maxi=40), langues,
        _champ('Activités', 'activites', v.get('activites'), 'Séparées par des virgules.', 400),
        _zone('Adresses propres à confirmer', 'adresses_propres', v.get('adresses_propres'), 'Une adresse par ligne, y compris les alias. Écrivez-les exactement comme elles sont utilisées.'),
        _zone('Signature', 'signature', v.get('signature'), 'Les coordonnées qu’on y trouve deviennent des propositions à confirmer.'),
        _modes(lu, v.get('profil_deploiement') or lu['profil_deploiement']), _boutons('identite', etat['revision'], retour=False), _alias_html(desk), _propositions_html(desk))


def _capacite_html(conn, cap):
    preuves = ''.join('<li>%s : %s%s</li>' % (e(prep.KINDS.get(k, k)), e(prep.RESULTATS.get(p['resultat'], p['resultat'])), (' (%s)' % e(p['date'][:10])) if p.get('date') else '')
                      for k, p in cap['preuves'].items())
    autoriser = ''
    if cap['nature'] != 'lecture':
        autoriser = ('<label class="c5689-case"><input type="checkbox" name="autoriser_creation"> J’autorise la création d’un élément de test, sans destinataire, relu puis retiré</label>')
    action = ''
    if cap['testable'] and cap['etat'] != 'revoquee':
        action = ('<form class="m5-form c5689-inline" data-api="m5689/connexions/%s/qualifier" data-reload><input type="hidden" name="capacite" value="%s"><input type="hidden" name="cle" value="%s">%s'
                  '<button class="ax-btn ghost" type="submit">Qualifier</button><p role="status" class="m5-status"></p></form>') % (e(conn['id'], quote=True), e(cap['capacite'], quote=True), _cle(), autoriser)
    if cap['etat'] == 'revoquee':
        action += ('<form class="m5-form c5689-inline" data-api="m5689/connexions/%s/reautoriser" data-reload data-confirm="Rétablir cette capacité ? Elle devra être qualifiée à nouveau."><input type="hidden" name="capacite" value="%s">'
                   '<input type="hidden" name="cle" value="%s"><button class="ax-btn ghost" type="submit">Réautoriser</button></form>') % (e(conn['id'], quote=True), e(cap['capacite'], quote=True), _cle())
    elif cap['preuves']:
        action += ('<form class="m5-form c5689-inline" data-api="m5689/connexions/%s/revoquer" data-reload data-confirm="Révoquer cette capacité ? Les étapes qui en dépendent seront suspendues ; les documents déjà créés restent en place."><input type="hidden" name="capacite" value="%s">'
                   '<input type="hidden" name="cle" value="%s"><button class="ax-btn ghost" type="submit">Révoquer</button></form>') % (e(conn['id'], quote=True), e(cap['capacite'], quote=True), _cle())
    correction = '<p class="ax-muted">%s</p>' % e(cap['correction']) if cap['correction'] else ''
    return ('<li class="c5689-cap"><strong>%s</strong> <span class="c5689-badge c5689-%s">%s</span>%s%s%s</li>' % (
        e(cap['libelle']), e(cap['etat']), e(cap['etat_libelle']), correction, ('<ul class="c5689-preuves">%s</ul>' % preuves) if preuves else '', action))


def _etape_outils(desk, etat, owner, role, prefix):
    cartes = ''
    for conn in prep.tableau(desk, owner, role):
        compte = (' <span class="ax-muted">%s</span>' % e(conn['compte'])) if conn['compte'] else ''
        cartes += '<article class="c5689-outil"><h4>%s%s</h4><ul class="c5689-liste">%s</ul><p><a href="%s">Régler cette connexion</a></p></article>' % (
            e(conn['libelle']), compte, ''.join(_capacite_html(conn, c) for c in conn['capacites']), e(prefix + conn['url'], quote=True))
    return ('<p class="ax-muted">Les adaptateurs sont déjà installés. Reliez chaque compte dans sa page de réglages : le mot de passe ou le jeton va au coffre, jamais dans le profil. Chaque capacité se prouve ensuite séparément ; '
            'aucun test n’envoie de courriel ni ne crée d’engagement payant.</p><div class="c5689-outils">%s</div>'
            '<form class="m5-form c5689-form" data-api="m5689/cabinet/assistant" data-reload>%s</form>') % (cartes, _boutons('outils', etat['revision']))


def _etape_modeles(desk, etat, owner):
    from . import competences5632 as comp
    lu = profil.lire(desk)
    choisis = etat['valeurs'].get('modeles', {})
    modeles_ok, competences_ok = set(choisis.get('template_refs', lu['template_refs'])), set(choisis.get('skill_refs', lu['skill_refs']))
    lignes_modeles = ''
    try:
        rows = desk.db.execute('SELECT id,label,version,status FROM cabinet_templates_v330 ORDER BY label,version DESC').fetchall()
    except Exception:   # noqa: BLE001 — table absente tant qu'aucun modèle n'a été importé
        rows = []
    for r in rows:
        lignes_modeles += '<label class="c5689-case"><input type="checkbox" name="modele:%s"%s> %s, version %s</label>' % (e(r['id'], quote=True), ' checked' if r['id'] in modeles_ok else '', e(r['label']), e(str(r['version'])))
    lignes_comp = ''
    for s in comp.listing(desk, owner):
        manque = (' <span class="ax-muted">Il manque : %s. Reliez ce service à l’étape précédente.</span>' % e(', '.join(s['missing']))) if s['missing'] else ''
        lignes_comp += '<label class="c5689-case"><input type="checkbox" name="competence:%s"%s> %s%s</label>' % (e(s['id'], quote=True), ' checked' if s['id'] in competences_ok else '', e(s['name']), manque)
    return ('<form class="m5-form c5689-form" data-api="m5689/cabinet/assistant" data-reload><h4>Modèles Word du cabinet</h4>%s<h4>Compétences</h4>%s'
            '<p class="ax-muted">Le choix du modèle d’IA et ses réglages restent dans la rubrique avancée « Modèles d’IA ».</p>%s</form>') % (
        lignes_modeles or '<p class="ax-muted">Aucun modèle Word importé pour l’instant : la rubrique Documents permet d’en ajouter.</p>', lignes_comp, _boutons('modeles', etat['revision']))


def _etape_demonstration(desk, etat, owner):
    demo = profil.derniere_demonstration(desk, owner)
    resultat = ''
    if demo:
        resultat = ('<section class="c5689-demo" aria-label="Résultat de la démonstration"><p class="c5689-marque">%s</p><h4>%s</h4><p>%s</p><ul>%s</ul><p>%s</p><p>%s</p><p class="ax-muted">%s</p>'
                    '<p><a class="ax-btn ghost" href="m5689-demo">Télécharger le document fictif</a></p></section>') % (
            e(demo['marque']), e(demo['titre']), e(demo['dossier']), ''.join('<li>%s</li>' % e(f) for f in demo['faits']), e(demo['analyse']), e(demo['suite']), e(demo['limites']))
    return ('<p class="ax-muted">La mission se déroule sur un dossier fictif et un modèle dans votre langue. Elle n’utilise aucun compte, n’appelle aucun modèle d’IA et ne lit ni n’écrit aucun vrai dossier.</p>'
            '<form class="m5-form c5689-form" data-api="m5689/cabinet/demonstration" data-reload><input type="hidden" name="cle" value="%s"><button class="ax-btn" type="submit">Lancer la mission fictive</button>'
            '<p role="status" class="m5-status"></p></form>%s<form class="m5-form c5689-form" data-api="m5689/cabinet/assistant" data-reload>%s</form>') % (
        _cle(), resultat, _boutons('demonstration', etat['revision']))


def _preparation_html(desk, owner, role):
    lignes = ''
    for key, spec in prep.MISSIONS.items():
        p = prep.preparation(desk, owner, role, key)
        if key == 'demonstration':
            continue
        manque = ('<br><span class="ax-muted">%s</span>' % e(p['prochaine_action'])) if p['prochaine_action'] else ''
        lignes += '<tr><th scope="row">%s</th><td><span class="c5689-badge c5689-%s">%s</span>%s</td></tr>' % (e(spec['label']), e(p['etat']), e(p['etat_libelle']), manque)
    return ('<h3>Ce qui est prêt, mission par mission</h3><div class="c5689-table"><table><tbody>%s</tbody></table></div>' % lignes)


def _manifeste_html(desk):
    m = profil.manifeste(desk)
    lignes = ''.join('<li>%s : %s. <span class="ax-muted">%s</span></li>' % (e(s['libelle']), 'inclus' if s['inclus'] else 'non inclus', e(s['detail'])) for s in m['services'])
    return '<details class="c5689-manifeste"><summary>Ce qui est réellement inclus ici (manifeste version %d)</summary><ul>%s</ul></details>' % (m['version'], lignes)


def settings_html(desk, auth, prefix, env):
    """Assistant en quatre étapes, repris à l'étape enregistrée, avec les saisies conservées."""
    from .web567 import actor
    owner, role = actor(env)
    etat = profil.assistant(desk)
    try:
        profil._peut_regler(desk, owner, role)
    except Stop:
        return ('<section class="ax-card" id="c5689"><h2>Démarrage du cabinet</h2><p>Votre compte n’a pas le droit « Réglages du cabinet » : demandez à l’administrateur de suivre cet assistant.</p>%s</section>'
                % _preparation_html(desk, owner, role))
    bloc = {'identite': lambda: _etape_identite(desk, etat), 'outils': lambda: _etape_outils(desk, etat, owner, role, prefix), 'modeles': lambda: _etape_modeles(desk, etat, owner),
            'demonstration': lambda: _etape_demonstration(desk, etat, owner)}[etat['etape']]()
    demo = profil.derniere_demonstration(desk, owner)
    if demo:
        bloc = bloc.replace('href="m5689-demo"', 'href="%s"' % e(prefix + '/api440/m5689/cabinet/demonstration/fichier?id=' + quote(demo['id']), quote=True))
    fil = ''
    for numero, step in enumerate(etat['etapes'], 1):
        marque = ' <span class="ax-muted">(faite)</span>' if step['faite'] else ''
        contenu = '%d. %s%s' % (numero, e(step['libelle']), marque)
        if step['courante']:
            fil += '<li aria-current="step"><strong>%s</strong></li>' % contenu
        else:
            fil += ('<li><form class="m5-form c5689-inline" data-api="m5689/cabinet/assistant" data-reload><input type="hidden" name="etape" value="%s"><input type="hidden" name="revision" value="%d">'
                    '<input type="hidden" name="action" value="aller"><button class="c5689-lien" type="submit">%s</button></form></li>') % (e(step['id'], quote=True), etat['revision'], contenu)
    termine = '<p class="c5689-fini" role="status">Assistant terminé. Vous pouvez y revenir à tout moment : les saisies sont conservées.</p>' if etat['statut'] == 'termine' else ''
    return ('<section class="ax-card" id="c5689"><h2>Démarrage du cabinet</h2><p class="ax-muted">Quatre étapes. La progression est enregistrée : vous reprendrez ici, sans rien ressaisir.</p>%s'
            '<ol class="c5689-etapes">%s</ol><h3>%s</h3>%s%s%s</section>') % (
        termine, fil, e(profil.LIBELLES_ETAPES[etat['etape']]), bloc, _preparation_html(desk, owner, role), _manifeste_html(desk))
