"""5.6.89 : routes ``/api440/m5689/benchmarks*`` et ``/api440/m5689/fonctions*``, et la page « Banc d'essai et état des fonctions » (Paramètres).

L'acteur et son rôle viennent de la session serveur, jamais du corps de la requête ; les services refont leurs contrôles. Lecture : administrateur et avocat pour le banc ; l'état des fonctions est ouvert à tous, dans leur périmètre.
Écriture : administrateur (droit « réglages »), sauf l'avis de relecteur, ouvert à l'avocat qui a le droit de valider. Tout passe par ``check_post`` ; révision périmée : 409 ; chaque effet porte une clé d'idempotence.

La page dit ce qui n'a pas été fait : aucun comparatif réel, aucune relecture humaine. Elle n'affiche que ce que le journal contient. Sans donnée, elle ne conclut pas.
"""
from html import escape as e
import re
from urllib.parse import quote
import uuid

from . import banc5689 as banc
from . import banc_corpus5689 as corpus
from . import fonctions5631 as registre
from . import fonctions_etat5689 as etat
from .common import Stop

ROUTE_RUN = re.compile(r'benchmarks/((?:reg|eva)-[0-9a-f]{32})(?:/(demarrer|clore|echouer|resultats|avis|couts|erreurs|comparaison|reception|recevoir|relecture|plan))?')
ROUTE_PROTOCOLE = re.compile(r'benchmarks/protocoles/([0-9a-f]{32})(?:/(cas|figer|nouvelle-version))?')
ROUTE_CONFIGURATION = re.compile(r'benchmarks/configurations/([0-9a-f]{32})')
ROUTE_FONCTION = re.compile(r'fonctions/([a-z_0-9]{1,60})')
LECTURES_RUN = ('', 'comparaison', 'reception', 'relecture', 'plan')
MESSAGES = {'protocole_fige': 'Ce protocole est figé : créez une nouvelle version pour le changer.', 'revision_perimee': 'Ces informations ont changé depuis leur ouverture : rechargez la page.',
            'corpus_altere': 'L’empreinte du corpus ne correspond plus au protocole figé : cet essai n’est pas fiable.', 'essai_api_presente_comme_desktop': 'Un essai par API ne peut pas être présenté comme un essai de Claude Desktop ou Cowork. Choisissez le canal réellement utilisé.',
            'capacites_a_consigner': 'Indiquez les compétences, connecteurs et sources utilisés, ou cochez « Aucune dans cette offre ».', 'memes_sources_requises': 'Les deux configurations doivent recevoir les mêmes sources autorisées.',
            'cas_hors_de_cette_phase': 'Ce cas appartient à l’autre partie du corpus.', 'essai_deja_saisi': 'Ce résultat est déjà enregistré ; il ne se réécrit pas.', 'reception_bloquee_erreur_critique': 'Une erreur critique a été relevée pour cette famille : elle ne peut pas être reçue.',
            'reception_sans_donnees_suffisantes': 'Les données ne suffisent pas pour recevoir cette famille.', 'cle_idempotence_requise': 'Cette action demande une clé d’essai valide : rechargez la page.', 'cle_idempotence_deja_utilisee': 'Cette clé a déjà servi à une autre action.',
            'cout_deja_compte': 'Ce coût est déjà compté.', 'credit_sans_justificatif': 'Un crédit se rattache à une facture, avec sa référence.', 'cas_identifiant': 'Un cas fictif ne contient ni adresse, ni téléphone, ni chemin de dossier réel.',
            'cas_autorise_sans_droits': 'Documentez les droits et les destinations autorisées de ce dossier.', 'cas_non_autorise_pour_cette_destination': 'Un cas de ce corpus n’est pas autorisé pour cette destination.', 'etat_essai_invalide': 'Cette action n’est pas possible à ce stade de l’essai.',
            'role_insuffisant': 'Votre compte n’a pas le droit de faire cela.', 'fonction_inconnue': 'Cette fonction n’existe pas.', 'version_incompatible': 'Cette version n’est plus compatible : requalifiez la connexion.'}
CONFLITS = ('protocole_fige', 'revision_perimee', 'essai_deja_saisi', 'cle_idempotence_deja_utilisee', 'etat_essai_invalide', 'cout_deja_compte', 'avis_deja_donne', 'cas_deja_present', 'corpus_altere', 'reception_bloquee_erreur_critique', 'reception_sans_donnees_suffisantes')
INTERDITS = ('role_insuffisant',)
ROLES = ('administrateur', 'avocat', 'assistant')


def _lecture_banc(role):
    return role in ('administrateur', 'avocat')


def allowed(role, route):
    """Refus par défaut. Une route connue reste à contrôler selon la méthode dans ``_autorise``."""
    route = route.split('?', 1)[0]
    if role not in ROLES:
        return False
    if route in ('fonctions', 'fonctions/conservation', 'fonctions/limites', 'fonctions/etapes') or ROUTE_FONCTION.fullmatch(route):
        return True
    connue = route in ('benchmarks', 'benchmarks/protocoles', 'benchmarks/configurations', 'benchmarks/exercice') or ROUTE_RUN.fullmatch(route) or ROUTE_PROTOCOLE.fullmatch(route) or ROUTE_CONFIGURATION.fullmatch(route)
    return bool(connue) and _lecture_banc(role)


def droit_requis(route, method):
    """Droit fin de ``membres5650.category`` pour les écritures de ces domaines."""
    if method in ('GET', 'HEAD'):
        return None
    match = ROUTE_RUN.fullmatch(route.split('?', 1)[0])
    return 'valider' if match and match.group(2) == 'avis' else 'reglages'


def _autorise(role, route, method):
    if method in ('GET', 'HEAD'):
        return True
    match = ROUTE_RUN.fullmatch(route)
    if match and match.group(2) == 'avis':
        return role in ('administrateur', 'avocat')
    return role == 'administrateur'


def _erreur(erreur):
    from .erreurs5634 import explain
    from .web440 import json_out
    code = str(erreur).split(':')[0]
    message = MESSAGES.get(code) or explain(code, getattr(erreur, 'detail', None))
    statut = '403 Forbidden' if code in INTERDITS else '409 Conflict' if code in CONFLITS else '404 Not Found' if code.endswith(('_absent', '_absente')) or code == 'fonction_inconnue' else '400 Bad Request'
    return json_out({'error': code, 'message': message}, statut)


def _lignes(valeur):
    if valeur is None:
        return []
    return [x.strip() for x in (valeur.splitlines() if isinstance(valeur, str) else [str(v) for v in valeur]) if x.strip()]


def trace_formulaire(data):
    """La page écrit une ligne par élément ; un programme peut envoyer ``trace`` directement. Case « non renseigné » : ``None``, jamais « rien ne s'est passé »."""
    if isinstance(data.get('trace'), dict):
        return data['trace']
    if data.get('trace_non_renseignee') in (True, 'on', '1'):
        return {}

    def paires(texte, cles):
        out = []
        for ligne in _lignes(texte):
            morceaux = [m.strip() for m in ligne.split(';', 1)] + ['']
            out.append({cles[0]: morceaux[0], cles[1]: morceaux[1]})
        return out

    return {'sources_fournies': _lignes(data.get('sources_fournies')), 'sources_utilisees': _lignes(data.get('sources_utilisees')), 'actions': paires(data.get('actions'), ('type', 'destinataire')),
            'references': paires(data.get('references'), ('ref', 'statut')), 'echeances': [{'date': r['date'], 'certaine': r['certaine'] in ('oui', 'certaine')} for r in paires(data.get('echeances'), ('date', 'certaine'))]}


def api(env, desk, auth, prefix, name, args, method):
    from .web440 import check_post, json_out
    from .web567 import actor
    owner, role = actor(env)
    route = name[len('m5689/'):].split('?', 1)[0]
    try:
        if not allowed(role, route) or not _autorise(role, route, method):
            raise Stop('role_insuffisant')
        if method in ('GET', 'HEAD'):
            return json_out(_lire(desk, owner, role, route, args))
        if method != 'POST':
            raise Stop('methode_refusee')
        return json_out(_ecrire(desk, owner, route, check_post(env, auth)))
    except Stop as ex:
        return _erreur(ex)


def _lire(desk, owner, role, route, args):
    if route == 'fonctions':
        return etat.tableau(desk, owner, role)
    if route == 'fonctions/conservation':
        return etat.conservation()
    if route == 'fonctions/limites':
        return {'limites': list(etat.LIMITES)}
    if route == 'fonctions/etapes':
        return etat.etapes(desk, owner, role, str(args.get('mission') or ''))
    fonction = ROUTE_FONCTION.fullmatch(route)
    if fonction:
        return {**etat.etat_fonction(desk, fonction.group(1), owner, role), 'historique': etat.historique(desk, fonction.group(1))}
    if route == 'benchmarks':
        return banc.tableau(desk)
    if route == 'benchmarks/protocoles':
        return {'protocoles': banc.tableau(desk)['protocoles']}
    if route == 'benchmarks/configurations':
        return {'configurations': banc.tableau(desk)['configurations']}
    protocole = ROUTE_PROTOCOLE.fullmatch(route)
    if protocole and not protocole.group(2):
        return banc.lire_protocole(desk, protocole.group(1))
    configuration = ROUTE_CONFIGURATION.fullmatch(route)
    if configuration:
        return banc.configuration(desk, configuration.group(1))
    run = ROUTE_RUN.fullmatch(route)
    if run and (run.group(2) or '') in LECTURES_RUN:
        ident, sous = run.group(1), run.group(2)
        return {None: lambda: banc.detail(desk, ident), 'comparaison': lambda: banc.comparaison(desk, ident), 'reception': lambda: banc.reception(desk, ident), 'relecture': lambda: {'sorties': banc.relecture(desk, ident)},
                'plan': lambda: {'plan': banc.plan(desk, ident)}}[sous]()
    raise Stop('route_inconnue')


def _ecrire(desk, owner, route, data):
    if route == 'fonctions/relever':
        return etat.relever(desk, owner)
    if route == 'benchmarks':
        return banc.ouvrir(desk, data, owner)
    if route == 'benchmarks/exercice':
        return banc.charger_exercice(desk, owner, data.get('cle'))
    if route == 'benchmarks/protocoles':
        return banc.creer_protocole(desk, {k: v for k, v in data.items() if not k.startswith('_')}, owner)
    if route == 'benchmarks/configurations':
        return banc.enregistrer_configuration(desk, data, owner)
    protocole = ROUTE_PROTOCOLE.fullmatch(route)
    if protocole and protocole.group(2):
        ident, sous = protocole.group(1), protocole.group(2)
        if sous == 'cas':
            return banc.ajouter_cas(desk, ident, data, owner, data.get('revision'))
        if sous == 'figer':
            return banc.figer(desk, ident, owner, data.get('revision'))
        return banc.nouvelle_version(desk, ident, owner, data.get('cle'))
    run = ROUTE_RUN.fullmatch(route)
    if not run or (run.group(2) or '') in LECTURES_RUN:
        raise Stop('route_inconnue')
    ident, sous = run.group(1), run.group(2)
    if sous == 'demarrer':
        return banc.demarrer(desk, ident, owner, data.get('revision'))
    if sous == 'clore':
        return banc.clore(desk, ident, owner, data.get('revision'))
    if sous == 'echouer':
        return banc.echouer(desk, ident, owner, data.get('revision'), data.get('motif'))
    if sous == 'resultats':
        return banc.saisir_essai(desk, ident, {**data, 'trace': trace_formulaire(data)}, owner)
    if sous == 'avis':
        return banc.donner_avis(desk, ident, data, owner)
    if sous == 'couts':
        return banc.ajouter_cout(desk, ident, data, owner)
    if sous == 'erreurs':
        return banc.signaler_erreur(desk, ident, data, owner)
    return banc.recevoir(desk, ident, str(data.get('famille') or ''), owner, data.get('cle'))


# ------------------------------------------------------------------------------------------------ page
def _champ(nom, libelle, valeur='', genre='text', aide=''):
    return '<label class="m5-field">%s<input name="%s" type="%s" value="%s">%s</label>' % (e(libelle), nom, genre, e(str(valeur), quote=True), ('<small class="ax-muted">%s</small>' % e(aide)) if aide else '')


def _zone(nom, libelle, aide=''):
    return '<label class="m5-field">%s<textarea name="%s" rows="2"></textarea>%s</label>' % (e(libelle), nom, ('<small class="ax-muted">%s</small>' % e(aide)) if aide else '')


def _choix(nom, libelle, options):
    return '<label class="m5-field">%s<select name="%s">%s</select></label>' % (e(libelle), nom, ''.join('<option value="%s">%s</option>' % (e(v, quote=True), e(t)) for v, t in options))


def _cle():
    return '<input type="hidden" name="cle" value="%s">' % uuid.uuid4().hex


def _formulaire(route, contenu, bouton, confirmer='', ghost=False):
    return ('<form class="m5-form" data-api="m5689/%s" data-reload%s>%s<button class="ax-btn%s" type="submit">%s</button><p role="status" class="m5-status"></p></form>'
            % (e(route, quote=True), (' data-confirm="%s"' % e(confirmer, quote=True)) if confirmer else '', contenu, ' ghost' if ghost else '', e(bouton)))


def _duree_txt(secondes):
    if secondes is None:
        return 'Non relevé'
    return '%d min %02d s' % (int(secondes) // 60, int(secondes) % 60)


def _micro(valeur):
    from .budget_ledger5689 import unites
    return ('%.2f' % unites(valeur)).replace('.', ',')


def _table(entetes, lignes, vide):
    if not lignes:
        return '<p class="ax-muted">%s</p>' % e(vide)
    return '<div class="table"><table><thead><tr>%s</tr></thead><tbody>%s</tbody></table></div>' % (''.join('<th>%s</th>' % e(t) for t in entetes), ''.join('<tr>%s</tr>' % ''.join('<td>%s</td>' % c for c in l) for l in lignes))


def _section_avertissement(desk):
    termine = desk.db.execute("SELECT COUNT(*) FROM banc_runs5689 WHERE phase='evaluation' AND state='completed'").fetchone()[0]
    avis = desk.db.execute('SELECT COUNT(*) FROM banc_avis5689').fetchone()[0]
    return ('<section class="ax-card" id="etat-du-comparatif"><h2>Où en est le comparatif</h2><p role="status"><strong>Le comparatif réel et la relecture humaine ne sont pas faits ici.</strong> Le logiciel est livré sans aucun résultat, score ni gain : '
            'les seuls cas fournis sont fictifs et servent à exercer l’outil. Ce journal compte %d essai(s) d’évaluation terminé(s) et %d avis de relecteur.</p>'
            '<p class="ax-muted">La cible de réduction du temps est un objectif de réception à confirmer, ni une performance observée ni une garantie. Un benchmark n’autorise pas à transmettre les dossiers du cabinet : '
            'utilisez des dossiers fictifs, ou autorisés pour chaque destination.</p></section>') % (termine, avis)


def _section_fonctions(desk, owner, role, prefix):
    t = etat.tableau(desk, owner, role)
    c = etat.conservation()
    compte = ''.join('<li>%s : %d</li>' % (e(t['libelles'][k]), n) for k, n in t['compte'].items())
    noms = {r['id']: r['label'] for r in registre.entries(desk.c, 'administrateur')}
    lignes = []
    for f in t['fonctions']:
        preuve = f['preuve'] or {}
        lignes.append((e(noms.get(f['id'], f['id'])), e(f['etat_libelle']) + ('' if f['perimetre_autorise'] else ' (hors de votre périmètre)'), 'Service externe' if f['externe'] else 'Interne', e(preuve.get('version') or 'Aucune'), e((preuve.get('date') or 'Aucune')[:10]), e(f['motif_libelle'])))
    conservation = ('<p role="status"><strong>%d fonctions sur %d conservées</strong> par rapport à la version %s. Ajouts : %s.</p>' % (c['conservees'], c['reference'], e(c['reference_version']), e(', '.join(noms.get(a, a) for a in c['ajouts']) or 'aucun'))
                    if c['ok'] else '<div role="alert"><p><strong>Le registre a perdu des fonctions ou des droits.</strong> Retraits : %s. Écarts : %d.</p></div>' % (e(', '.join(c['retraits']) or 'aucun'), len(c['ecarts'])))
    limites = ''.join('<tr><td>%s</td><td>%s</td><td>%s</td></tr>' % (e(l['titre']), e(l['garantie']), e(l['essai'])) for l in etat.LIMITES)
    return ('<section class="ax-card" id="fonctions"><h2>État des fonctions</h2><p class="ax-muted">Cette version ne compte aucune fonction externe qualifiée sur service réel : une preuve de connexion montre qu’un compte répond, pas que la fonction a été qualifiée. '
            'Ce tableau décrit les fonctions livrées ; les propositions d’assistance IA n’y figurent pas.</p>%s<ul>%s</ul><details><summary>Détail par fonction (%d)</summary>%s</details>%s</section>'
            '<section class="ax-card" id="limites"><h2>Reste à qualifier sur de vrais services</h2><p class="ax-muted">Ces points ne sont pas des pertes de données constatées : ce sont des garanties qui ne sont pas encore démontrées.</p>'
            '<div class="table"><table><thead><tr><th>Périmètre</th><th>Où en est la garantie</th><th>Essai à mener</th></tr></thead><tbody>%s</tbody></table></div></section>') % (
        conservation, compte, len(t['fonctions']), _table(('Fonction', 'État', 'Nature', 'Version de la preuve', 'Date de la preuve', 'Motif'), lignes, 'Aucune fonction.'),
        _formulaire('fonctions/relever', '', 'Conserver ce relevé', ghost=True) if role == 'administrateur' else '', limites)


def _section_protocoles(t, admin):
    lignes, formes = [], ''
    for p in t['protocoles']:
        cv = p['couverture']
        lignes.append((e(p['nom']), str(p['version']), 'Figé le %s' % e(p['fige_le'][:10]) if p['etat'] == 'fige' else 'Brouillon', 'Exercice fictif' if p['exercice'] else 'Protocole du cabinet', '%d sur %d' % (cv['total'], cv['cible']),
                      ('<code>%s</code>' % e(p['corpus_sha256'][:16])) if p['corpus_sha256'] else 'Non calculée', 'Intact' if p['corpus_intact'] else '<strong>Altéré</strong>'))
        if admin and p['etat'] == 'brouillon':
            formes += '<h3>%s, version %d</h3>' % (e(p['nom']), p['version']) + _formulaire('benchmarks/protocoles/%s/figer' % p['id'], '<input type="hidden" name="revision" value="%d">' % p['revision'], 'Figer ce protocole',
                                                                                         confirmer='Une fois figé, ce protocole ne se modifie plus. Continuer ?')
    familles = ''
    if t['protocoles']:
        familles = _table(('Famille', 'Réglage', 'Évaluation'), [(e(f['libelle']), '%d sur %d' % (f['reglage'], f['reglage_cible']), '%d sur %d' % (f['evaluation'], f['evaluation_cible'])) for f in t['protocoles'][0]['couverture']['familles']], '')
    actions = ''
    if admin:
        actions = (_formulaire('benchmarks/exercice', _cle(), 'Charger le corpus d’exercice fictif', ghost=True) + _formulaire('capacites/pertinence', _cle(), 'Charger les situations de pertinence, exercice fictif', ghost=True) +
                   '<details><summary>Nouveau protocole</summary>' + _formulaire('benchmarks/protocoles', _cle() + _champ('nom', 'Nom') + _champ('effectif_minimal', 'Effectif minimal par famille', banc.EFFECTIF_MINIMAL_DEFAUT, 'number') +
                                                                                 _zone('parcours', 'Parcours prioritaires', 'Une famille par ligne : %s' % ', '.join(corpus.FAMILLES)), 'Créer le protocole') + '</details>')
    return ('<section class="ax-card" id="protocoles"><h2>Protocoles et corpus</h2><p class="ax-muted">Le protocole se fige avant le premier essai : cas, sources autorisées, livrables, faits attendus, réserves, critères, actions permises et erreurs critiques. '
            'Le cahier demande au moins %d situations ; le corpus d’exercice en compte %d et ne permet aucune réception.</p>%s%s%s%s</section>') % (
        banc.SITUATIONS_MINIMALES, len(corpus.CAS), _table(('Protocole', 'Version', 'État', 'Nature', 'Situations', 'Empreinte', 'Corpus'), lignes, 'Aucun protocole : chargez le corpus d’exercice pour essayer l’outil.'), familles, formes, actions)


def _section_configurations(t, admin):
    lignes = [(e(c['intitule']), e(c['produit']), e(c['offre']), e(c['version']), e(c['modele']), e(', '.join(sum(c['capacites'].values(), [])) or 'Aucune déclarée'), e(c['intervention_humaine'] or 'Non indiquée'), _duree_txt(c['preparation_s'])) for c in t['configurations']]
    formulaire = ''
    if admin:
        formulaire = ('<details><summary>Consigner une configuration</summary>' + _formulaire('benchmarks/configurations', _cle() + _choix('canal', 'Canal réellement utilisé', list(banc.CANAUX.items())) +
                      _champ('produit', 'Produit') + _champ('offre', 'Offre ou abonnement') + _champ('version', 'Version de l’application') + _champ('modele', 'Modèle') + _zone('parametres', 'Paramètres') +
                      _zone('competences', 'Compétences', 'Une par ligne') + _zone('connecteurs', 'Connecteurs', 'Un par ligne') + _zone('sources', 'Sources', 'Une par ligne') +
                      '<label class="m5-field"><input type="checkbox" name="aucune_capacite"> Aucune dans cette offre</label><label class="m5-field"><input type="checkbox" name="memes_sources"> Reçoit les sources autorisées du protocole, ni plus ni moins</label>' +
                      _zone('intervention_humaine', 'Intervention humaine') + _champ('preparation_s', 'Installation et préparation initiale, en secondes', '', 'number', 'Mesurée à part de l’usage quotidien'), 'Enregistrer la configuration') +
                      '<p class="ax-muted">Pour AxiorHub, la version, le modèle et la politique sont relevés par le logiciel.</p></details>')
    return ('<section class="ax-card" id="configurations"><h2>Configurations comparées</h2><p class="ax-muted">Une référence se consigne telle qu’elle a été utilisée : offre, version, modèle, capacités et intervention humaine. Un essai par API n’est jamais un essai de Claude Desktop ou Cowork.</p>%s%s</section>'
            ) % (_table(('Canal', 'Produit', 'Offre', 'Version', 'Modèle', 'Capacités', 'Intervention humaine', 'Préparation initiale'), lignes, 'Aucune configuration consignée.'), formulaire)


def _section_essais(t, admin, prefix):
    lignes = [('<a href="%s/banc-essai?essai=%s">%s</a>' % (e(prefix, quote=True), quote(x['contrat']['run_id']), e(x['contrat']['run_id'][:12])), e(x['phase_libelle']), e(x['etat_libelle']), str(x['contrat']['number_of_cases']), str(len(x['contrat']['metric_artifact_refs']))) for x in t['essais']]
    formulaire = ''
    figes = [p for p in t['protocoles'] if p['etat'] == 'fige']
    candidats = [c for c in t['configurations'] if c['role'] == 'candidat']
    references = [c for c in t['configurations'] if c['role'] == 'reference']
    if admin and figes and candidats and references:
        formulaire = '<details><summary>Prévoir un essai</summary>' + _formulaire('benchmarks', _cle() + _choix('protocole', 'Protocole figé', [(p['id'], '%s, version %d' % (p['nom'], p['version'])) for p in figes]) + _choix('phase', 'Partie du corpus', list(banc.PHASES.items())) +
                                                                                 _choix('candidat', 'AxiorHub', [(c['id'], '%s %s' % (c['produit'], c['version'])) for c in candidats]) + _choix('reference', 'Référence', [(c['id'], '%s, %s' % (c['intitule'], c['offre'])) for c in references]) +
                                                                                 _zone('droits', 'Droits et limites de diffusion'), 'Prévoir l’essai') + '</details>'
    elif admin:
        formulaire = '<p class="ax-muted">Pour prévoir un essai, il faut un protocole figé, une configuration AxiorHub et une référence.</p>'
    return '<section class="ax-card" id="essais"><h2>Essais</h2>%s%s</section>' % (_table(('Essai', 'Partie', 'État', 'Cas', 'Résultats'), lignes, 'Aucun essai.'), formulaire)


def _bloc_familles(comp):
    def med(r):
        return 'Aucune mesure' if r['mediane'] is None else '%s %% (de %s à %s)' % (r['mediane'], r['q1'], r['q3'])
    lignes = [(e(f['libelle']), str(f['cas']), str(f['reduction_pct']['effectif']), med(f['reduction_pct']), ' / '.join(str(f['candidat'][k]) for k in banc.STATUTS), ' / '.join(str(f['reference'][k]) for k in banc.STATUTS), str(f['non_comparables']), str(f['erreurs_critiques'])) for f in comp['familles']]
    conclusion = comp['conclusion']
    if conclusion['possible']:
        texte = '<p role="status">%s</p><ul>%s</ul>' % (e(conclusion['limite']), ''.join('<li>%s : %s %%, cible %s %%, %s</li>' % (e(corpus.FAMILLES[p['famille']]), p['reduction_mediane_pct'], p['cible_pct'], 'atteinte' if p['atteinte'] else 'non atteinte') for p in conclusion['parcours']))
    else:
        texte = '<p role="status"><strong>Aucune conclusion possible.</strong></p><ul>%s</ul>' % ''.join('<li>%s</li>' % e(m) for m in conclusion['motifs_libelles'])
    return _table(('Famille', 'Cas', 'Paires mesurées', 'Réduction médiane du temps (quartiles)', 'AxiorHub : rejeté / partiel / exploitable', 'Référence : rejeté / partiel / exploitable', 'Non comparables', 'Erreurs critiques'), lignes, 'Aucun cas.') + texte


def _section_essai(desk, ident, admin, avocat):
    d = banc.detail(desk, ident)
    r = d['run']
    ref, cand = d['reference'], d['candidat']
    entete = ('<section class="ax-card" id="essai"><h2>Essai %s</h2><p>%s, %s. Protocole %s, version %s. Empreinte du corpus : <code>%s</code></p><p>AxiorHub %s, modèle %s. Référence : %s, offre %s, version %s, modèle %s.</p>%s') % (
        e(r['run_id'][:12]), e(d['phase_libelle']), e(banc.ETATS_RUN[r['state']]), e(d['protocole']['nom']), e(str(d['protocole']['version'])), e(r['dataset_sha256'][:16]), e(cand['version']), e(cand['modele']), e(ref['intitule']), e(ref['offre']), e(ref['version']), e(ref['modele']),
        ('<p role="status">Essai échoué : %s</p>' % e(d['motif_echec'])) if d['motif_echec'] else '')
    actions = ''
    revision = '<input type="hidden" name="revision" value="%d">' % d['revision']
    if admin and r['state'] == 'planned':
        actions = _formulaire('benchmarks/%s/demarrer' % ident, revision, 'Démarrer l’essai')
    if admin and r['state'] == 'running':
        cas = [(x['cas'], x['cas']) for x in d['plan']]
        ordres = {x['cas']: x for x in d['plan']}
        premier = ', '.join('%s : %s' % (c, 'AxiorHub d’abord' if ordres[c]['premier'] == cand['id'] else 'référence d’abord') for c, _ in cas)
        actions = ('<h3>Saisir un résultat</h3><p class="ax-muted">Ordre alterné prévu : %s.</p>' % e(premier) + _formulaire('benchmarks/%s/resultats' % ident, _cle() + _choix('cas', 'Cas', cas) + _choix('configuration', 'Produit', [(cand['id'], 'AxiorHub'), (ref['id'], ref['intitule'])]) +
                   _choix('ordre', 'Passé en', [('1', 'Premier'), ('2', 'Second')]) + _choix('statut', 'Résultat', [(k, v) for k, v in banc.STATUTS.items()]) + _champ('preparation_s', 'Temps humain de préparation, en secondes', '', 'number') + _champ('execution_s', 'Durée d’exécution, en secondes', '', 'number') +
                   _champ('correction_s', 'Temps de correction, en secondes', '', 'number') + _champ('omissions', 'Omissions', '', 'number') + _champ('incidents', 'Incidents', '', 'number') + _champ('fidelite', 'Fidélité aux sources') + _zone('intervention', 'Intervention humaine') +
                   _champ('livrable_ref', 'Référence du livrable') + _champ('livrable_version', 'Version du livrable') + _champ('livrable_sha256', 'Empreinte SHA-256 du livrable') +
                   _zone('sources_fournies', 'Sources fournies', 'Une par ligne') + _zone('sources_utilisees', 'Sources utilisées', 'Une par ligne') + _zone('actions', 'Actions effectuées', 'Une par ligne : action ; destinataire') +
                   _zone('references', 'Références citées', 'Une par ligne : référence ; verifiee ou a_verifier') + _zone('echeances', 'Échéances annoncées', 'Une par ligne : date ; certaine ou incertaine') +
                   '<label class="m5-field"><input type="checkbox" name="trace_non_renseignee"> Trace non relevée pour cet essai</label>', 'Enregistrer ce résultat') +
                   _formulaire('benchmarks/%s/clore' % ident, revision, 'Terminer l’essai', ghost=True, confirmer='Aucun résultat ne pourra plus être ajouté. Continuer ?'))
    comparaison = '<h3>Comparaison</h3>' + _bloc_familles(d['comparaison'])
    detail_cas = [(e(c['cas']), e(corpus.FAMILLES[c['famille']]), _duree_txt((c['candidat'] or {}).get('temps_total_s')), _duree_txt((c['reference'] or {}).get('temps_total_s')), '%s %%' % c['reduction_pct'] if c['reduction_pct'] is not None else e(banc.MOTIFS_CAS.get(c['motif'], 'En attente')), 'Oui' if c['relu'] else 'Non') for c in d['comparaison']['cas']]
    comparaison += '<details><summary>Détail par cas</summary>%s</details>' % _table(('Cas', 'Famille', 'Temps AxiorHub', 'Temps référence', 'Réduction ou motif', 'Relu'), detail_cas, 'Aucun cas.')
    relecture = ''
    if (admin or avocat) and d['resultats']:
        options = [(s['essai'], 'Cas %s, sortie %s' % (s['cas'], s['sortie'])) for s in banc.relecture(desk, ident)]
        relecture = '<h3>Avis de relecteur</h3><p class="ax-muted">Les sorties sont désignées par une lettre. Si la mise à l’aveugle n’a pas été obtenue, dites-le.</p>' + _formulaire('benchmarks/%s/avis' % ident, _cle() + _choix('essai', 'Sortie', options) + _choix('verdict', 'Avis', list(banc.STATUTS.items())) +
                     '<label class="m5-field"><input type="checkbox" name="aveugle"> J’ai relu sans connaître le produit</label>' + _champ('motif_non_aveugle', 'Sinon, pourquoi') + _zone('commentaire', 'Commentaire'), 'Enregistrer mon avis')
        relecture += '<details><summary>Signaler une erreur critique</summary>' + _formulaire('benchmarks/%s/erreurs' % ident, _choix('essai', 'Sortie', options) + _choix('categorie', 'Erreur', list(banc.ERREURS_CRITIQUES.items())) + _zone('detail', 'Constat'), 'Signaler l’erreur', ghost=True) + '</details>' if admin else ''
    couts = _table(('Produit', 'Mesuré', 'Facturé', 'Estimé', 'Crédits', 'Inconnus', 'Correction observée'), [(e('AxiorHub' if c == cand['id'] else ref['intitule']), *[e(', '.join('%s %s' % (_micro(v), k) for k, v in b[n].items()) or 'Aucun') for n in ('mesure', 'facture', 'estime', 'credits')], str(b['inconnus']), _duree_txt(b['correction_observee_s'])) for c, b in d['couts']['par_configuration'].items()], '')
    if admin and r['state'] in ('running', 'completed'):
        couts += '<details><summary>Ajouter un coût</summary>' + _formulaire('benchmarks/%s/couts' % ident, _cle() + _choix('configuration', 'Produit', [(cand['id'], 'AxiorHub'), (ref['id'], ref['intitule'])]) + _choix('nature', 'Nature', list(banc.NATURES_COUT.items())) +
                                                                             _choix('origine', 'Origine', [('facture', 'Facture'), ('estime', 'Estimation'), ('inconnu', 'Inconnu')]) + _champ('montant', 'Montant') + _champ('devise', 'Devise', 'USD') + _champ('reference', 'Référence de la facture ou du crédit') +
                                                                             _champ('call_id', 'Ou identifiant d’appel AxiorHub', '', 'text', 'Le montant est alors lu au registre du budget'), 'Ajouter ce coût') + '</details>'
    recep = d['reception']
    lignes = []
    for f in recep['familles']:
        bouton = ''
        if admin and f['etat'] == 'recevable' and not f['recue']:
            bouton = _formulaire('benchmarks/%s/recevoir' % ident, _cle() + '<input type="hidden" name="famille" value="%s">' % e(f['famille'], quote=True), 'Recevoir cette famille')
        lignes.append((e(f['libelle']), {'bloquee': '<strong>Bloquée</strong>', 'sans_conclusion': 'Sans conclusion', 'recevable': 'Recevable'}[f['etat']], str(f['erreurs_critiques']), e(' '.join(f['motifs_libelles'])), {'recue': 'Reçue', 'a_requalifier': 'À requalifier', '': 'Non'}[f['recue']] + bouton))
    erreurs = ''.join('<li>Cas %s : %s (%s)</li>' % (e(x['cas']), e(banc.ERREURS_CRITIQUES.get(x['categorie'], x['categorie'])), e(x['detail'])) for x in recep['erreurs'])
    reception = '<h3>Réception</h3>%s<ul>%s</ul><p class="ax-muted">%s</p>' % (_table(('Famille', 'État', 'Erreurs critiques', 'Motifs', 'Reçue'), lignes, 'Aucune famille.'), erreurs, e(recep['limite']))
    journal = ''.join('<li>%s : %s%s</li>' % (e(x['at'][:19].replace('T', ' ')), e(banc.EVENEMENTS.get(x['genre'], x['genre'])), (', cas ' + e(x['cas'])) if x['cas'] else '') for x in d['evenements'][-30:])
    return entete + actions + comparaison + relecture + '<h3>Coûts</h3>' + couts + reception + '<details><summary>Journal</summary><ul>%s</ul></details></section>' % journal


def _section_sources():
    return ('<section class="ax-card" id="references"><h2>Références consultées</h2><p class="ax-muted">Ces pages décrivent les offres consultées le 2 novembre 2026 ; ce ne sont pas des mesures de qualité. Chaque essai consigne l’offre réellement utilisée.</p><ul>%s</ul></section>'
            ) % ''.join('<li><a href="%s" rel="noopener noreferrer" target="_blank">%s</a></li>' % (e(u, quote=True), e(t)) for t, u in SOURCES_COMPAREES)


SOURCES_COMPAREES = (('Anthropic Legal plugin', 'https://claude.com/marketplace/plugins/legal'), ('Use plugins in Claude', 'https://support.claude.com/en/articles/13837440-use-plugins-in-claude'),
                     ('Is my data used for model training', 'https://privacy.claude.com/en/articles/7996868-is-my-data-used-for-model-training'),
                     ('How long do you store my organization data', 'https://privacy.claude.com/en/articles/7996866-how-long-do-you-store-my-organization-s-data'),
                     ('How do I pay for my Claude API usage', 'https://support.claude.com/en/articles/8977456-how-do-i-pay-for-my-claude-api-usage'))


def page(desk, auth, prefix, env, args):
    from .web567 import actor
    owner, role = actor(env)
    banc.ensure_schema(desk)
    if not _lecture_banc(role):
        return '<h1>État des fonctions</h1>' + _section_fonctions(desk, owner, role, prefix)
    admin, avocat = role == 'administrateur', role == 'avocat'
    t = banc.tableau(desk)
    ident = str(args.get('essai') or '')
    detail = ''
    if ident:
        try:
            detail = _section_essai(desk, ident, admin, avocat)
        except Stop:
            detail = '<section class="ax-card"><p role="alert">Cet essai n’existe pas.</p></section>'
    return ('<h1>Banc d’essai et état des fonctions</h1><p class="ax-muted">Comparer des missions complètes sur un protocole figé, et savoir ce que chaque fonction a réellement prouvé.</p>' +
            _section_avertissement(desk) + detail + _section_fonctions(desk, owner, role, prefix) + _section_protocoles(t, admin) + _section_configurations(t, admin) + _section_essais(t, admin, prefix) + _section_sources())
