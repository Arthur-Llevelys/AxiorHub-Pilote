"""5.6.89 : routes ``/api440/m5689/livrables/*`` et ``/documents/*``, pages ``/livrables`` et ``/livrables/<id>`` (visionneuse commune LivrableViewer).

Le compte et le rôle viennent de la requête authentifiée, jamais du corps. Chaque action résout le document côté serveur, puis contrôle dossier réservé et droits : une révocation vaut à l'appel suivant.
Aucune route ne reçoit un chemin : le navigateur ne manipule que des identifiants opaques et des références de destination. Les pages restent sobres, le lot Carbon les habillera.
"""
from html import escape as e
import json
import re
from urllib.parse import urlencode, urlsplit
import uuid

from .common import Stop
from . import livrables5689 as liv, livrables_apercu5689 as apercu, livrables_export5689 as export, propositions5689 as patches, verification5689 as verification

WRITE = {'livrables': ('modifier-ia', 'exporter', 'relecture', 'copie-travail'), 'documents': ('proposition', 'controler')}
LAWYER_ONLY = ('decider', 'inverser')
STATUS = {'version_perimee': '409 Conflict', 'conflit_edition_concurrente': '409 Conflict', 'cle_idempotence_deja_utilisee': '409 Conflict', 'version_plus_disponible': '409 Conflict', 'proposition_deja_traitee': '409 Conflict',
          'dossier_reserve': '403 Forbidden', 'role_insuffisant': '403 Forbidden', 'droit_export_retire': '403 Forbidden', 'validation_avocat_requise': '403 Forbidden', 'original_protege': '403 Forbidden', 'destination_non_autorisee': '403 Forbidden',
          'livrable_absent': '404 Not Found', 'version_inconnue': '404 Not Found', 'proposition_absente': '404 Not Found', 'export_absent': '404 Not Found', 'route_inconnue': '404 Not Found', 'fichier_nextcloud_introuvable': '404 Not Found', 'http_404': '404 Not Found'}
MESSAGES = {'version_perimee': 'Le document a changé depuis votre affichage : rechargez la page ; rien n’a été écrasé.',
            'conflit_edition_concurrente': 'Quelqu’un a modifié le document pendant ce temps : la proposition n’a pas été appliquée. Comparez les versions avant de choisir.',
            'version_plus_disponible': 'Cette version n’est plus dans le fichier : aucune autre version n’est servie à sa place. L’historique de versions du stockage permet de la retrouver.',
            'fichier_pas_encore_cree': 'Le fichier n’existe pas encore, ou n’a pas été vérifié : rien à télécharger pour l’instant.',
            'conversion_indisponible': 'Conversion indisponible sur cette installation (LibreOffice absent ou en échec). Le document reste disponible dans son format d’origine ; votre travail n’est pas perdu.',
            'format_non_disponible': 'Ce format n’est pas proposé pour ce document.', 'conversion_non_verifiee': 'Le fichier converti n’a pas passé le contrôle de réouverture : il n’a pas été remis.',
            'destination_non_autorisee': 'Cette destination n’est pas autorisée pour ce document.', 'original_protege': 'Original reçu, signé ou émis : il n’est jamais modifié. Créez une copie de travail.',
            'format_non_modifiable': 'Seuls les documents Word se modifient par proposition : créez une copie de travail ou un nouveau projet.', 'parcours_dedie_facturation': 'Ce document se corrige dans le parcours de facturation.',
            'droit_export_retire': 'Votre compte n’a pas le droit d’exporter ou de télécharger.', 'cle_idempotence_absente': 'Requête incomplète : rechargez la page et recommencez.', 'cle_idempotence_deja_utilisee': 'Cette demande a déjà servi pour une autre action : rechargez la page.',
            'livrable_absent': 'Ce document n’est pas ou plus au registre.', 'version_inconnue': 'Cette version n’est pas connue du registre.', 'proposition_absente': 'Cette proposition n’existe pas ou plus.', 'proposition_deja_traitee': 'Cette proposition a déjà été traitée.',
            'instruction_vide_ou_trop_longue': 'Décrivez la modification souhaitée (3 à 2000 caractères).', 'version_de_base_absente': 'Version de base absente : rechargez la page.', 'curseur_invalide': 'Page de résultats invalide.', 'filtre_invalide': 'Filtre invalide.',
            'passage_structure_a_preserver': 'Ce paragraphe contient un renvoi, une image ou un lien qu’une réécriture entière ferait disparaître : proposez de modifier seulement le texte concerné.', 'proposition_non_inversable': 'Cette modification comporte un retrait : elle ne s’annule pas automatiquement. Comparez les versions et rédigez la correction.',
            'cible_ambigue_ou_absente': 'Le passage visé est introuvable ou n’est pas unique dans le document.', 'ancre_word_absente': 'Le paragraphe visé est introuvable dans cette version.', 'ancre_word_ambigue': 'Le paragraphe visé apparaît plusieurs fois : précisez-le.'}
DEPOT = {'depose': 'Brouillon déposé dans la messagerie', 'a_decider': 'Réponse à décider, pas encore déposée', 'retire': 'Brouillon retiré de la messagerie'}
SOURCE_STATE = {'fresh': 'À jour', 'stale': 'Ancien', 'unavailable': 'Source indisponible', 'partial': 'Lecture partielle', 'empty': 'Vide', 'running': 'En cours'}


def allowed(role, route):
    """Toute écriture est déclarée ici, inconnue refusée ; le service refait ses propres contrôles."""
    if role not in ('administrateur', 'avocat', 'assistant'):
        return False
    parts = route.split('/')
    if parts[0] == 'documents' and len(parts) == 4 and parts[1] == 'propositions':
        return parts[3] in LAWYER_ONLY and role in ('administrateur', 'avocat')
    if len(parts) == 3:
        return parts[2] in WRITE.get(parts[0], ())
    return False


def _out(value, status='200 OK'):
    from .web440 import json_out
    return json_out(value, status)


def _erreur(ex):
    from .erreurs5634 import explain
    from .web import REASONS
    code = str(ex).split(':')[0]
    body = {'error': code, 'message': MESSAGES.get(code) or REASONS.get(code) or explain(code, getattr(ex, 'detail', None))}
    if isinstance(getattr(ex, 'detail', None), dict) and ex.detail.get('cause'):
        body['conflit'] = ex.detail
    return _out(body, STATUS.get(code, '400 Bad Request'))


def _contexte(desk, env):
    from .membres5650 import effective_rights, hidden_for
    from .web567 import actor
    owner, role = actor(env)
    return owner, role, hidden_for(desk.c['state_dir'], owner, role), effective_rights(desk.c['state_dir'], owner, role)


def api(env, desk, auth, prefix, name, args, method):
    try:
        return _api(env, desk, auth, prefix, name, args, method)
    except Stop as ex:
        return _erreur(ex)


def _exiger_export(droits):
    if not droits.get('exporter'):
        raise Stop('droit_export_retire')


def _api(env, desk, auth, prefix, name, args, method):
    from .web440 import check_post
    owner, role, hidden, droits = _contexte(desk, env)
    route = name[len('m5689/'):].split('?', 1)[0].strip('/')
    parts = route.split('/')
    if role not in ('administrateur', 'avocat', 'assistant'):
        raise Stop('role_insuffisant')
    if method == 'GET':
        return _lecture(desk, prefix, parts, args, owner, role, hidden, droits)
    if method != 'POST' or not allowed(role, route):
        raise Stop('route_inconnue' if method == 'POST' else 'methode_refusee')
    data = check_post(env, auth)
    if parts[0] == 'documents' and parts[1] == 'propositions':
        return _proposition(desk, parts, data, owner, role, hidden, droits)
    row = liv.charger(desk, parts[1], hidden, droits=droits)
    action = parts[2]
    if action == 'relecture':
        return _out(_relecture(desk, row, data, owner, role))
    if action == 'exporter':
        _exiger_export(droits)
        if row['version_id'] != str(data.get('version_id') or row['version_id']):
            raise Stop('version_perimee')
        out = export.demander(desk, row, data.get('format'), data.get('destination'), data.get('cle'), owner)
        return _out({**out, 'message': 'Export en cours : il sera indiqué « Exporté » une fois le fichier relu.' if not out['done'] else 'Déjà exporté.'})
    if action == 'modifier-ia':
        out = patches.demander_ia(desk, row, data, owner)
        return _out({**out, 'message': 'Demande enregistrée : une proposition à comparer apparaîtra ici. Rien n’est modifié sans votre décision.'})
    if action == 'copie-travail':
        copy = patches.copie_de_travail(desk, row, owner, data.get('cle'))
        return _out({'document_id': copy['document_id'], 'href': prefix + '/livrables/' + copy['document_id'], 'message': 'Copie de travail créée ; l’original n’a pas été touché.'})
    if action == 'proposition':
        return _out({**patches.proposer(desk, row, data, owner), 'message': 'Proposition enregistrée, à décider.'})
    if action == 'controler':
        job = desk.enqueue('controle_livrable5689', {'document_id': row['document_id'], 'version_id': row['version_id'], 'matter': row['matter_id']})
        return _out({'job_id': job, 'message': 'Contrôles lancés (travail n° %d).' % job})
    raise Stop('route_inconnue')


def _relecture(desk, row, data, owner, role):
    decision = str(data.get('decision') or '')
    item = liv.relire(desk, row['document_id'], data.get('version_id'), decision, owner, role, data.get('note') or '')
    personal = not desk.db.execute("SELECT 1 FROM prepared_reviews5689 WHERE document_id=? AND version_id=? AND scope='cabinet' LIMIT 1", (row['document_id'], row['version_id'])).fetchone()
    return {'review': item['review_state'], 'version_id': item['version_id'], 'message': 'Lecture notée pour vous : la décision du cabinet reste à prendre.' if personal else 'Décision enregistrée sur cette version.'}


def _proposition(desk, parts, data, owner, role, hidden, droits):
    patch = patches._get(desk, parts[2])
    liv.charger(desk, patch['document_id'], hidden, droits=droits)
    if not droits.get('valider'):
        raise Stop('validation_avocat_requise')
    if parts[3] == 'inverser':
        return _out({**patches.inverser(desk, patch['id'], owner, data.get('cle')), 'message': 'Proposition inverse enregistrée, à décider.'})
    out = patches.decider(desk, patch['id'], str(data.get('decision') or ''), owner, role, data.get('version_id') or '', data.get('sha256') or '', data.get('texte'))
    return _out({**out, 'message': out['state_label'] + '.'})


def _lecture(desk, prefix, parts, args, owner, role, hidden, droits):
    if parts[0] == 'documents':
        if len(parts) == 3 and parts[2] == 'verification':
            return _out(verification.vue(desk, liv.charger(desk, parts[1], hidden, droits=droits)))
        if len(parts) == 3 and parts[2] == 'propositions':
            row = liv.charger(desk, parts[1], hidden, droits=droits)
            return _out({'propositions': patches.liste(desk, row['document_id']), 'note': patches.NOTE_COMPARAISON})
        raise Stop('route_inconnue')
    if len(parts) == 1:
        return _out(_projection(desk, args, hidden, droits, prefix))
    if parts[1] == 'revision':
        return _out({'revision': liv.revision(desk, hidden, droits, args.get('dossier') or None)})
    if parts[1] == 'exports' and len(parts) == 3:
        row = export.etat(desk, parts[2])
        liv.charger(desk, row['document_id'], hidden, droits=droits)
        return _out(export._public(row))
    version = str(args.get('version') or '')
    row = liv.charger(desk, parts[1], hidden, version_id=version or None, droits=droits)
    if len(parts) == 2:
        return _out({'view': apercu.vue(desk, row, owner, role), 'card': liv.carte(row, *liv._labels(desk), (), desk, prefix), 'verification': verification.vue(desk, row), 'versions': _versions(desk, row)})
    action = parts[2]
    if action == 'apercu':
        out = apercu.apercu(desk, row, prefix, version_demandee=version)
        return _out({k: v for k, v in out.items() if k != 'row'})
    if action == 'cadre':
        out = apercu.apercu(desk, row, prefix, version_demandee=version)
        if not out['html']:
            raise Stop('apercu_format_non_pris_en_charge')
        return apercu.cadre(out['html'])
    if action == 'telecharger':
        _exiger_export(droits)
        desk.audit('livrable5689_telecharge', {'document': row['document_id'][:40], 'version': row['version_id'][:40], 'par': str(owner)[:80]})
        return export.telecharger(desk, row, str(args.get('format') or ''), str(args.get('apercu') or '') == '1', version)
    raise Stop('route_inconnue')


def _projection(desk, args, hidden, droits, prefix):
    return liv.projection(desk, hidden, droits, args.get('dossier') or None, str(args.get('nature') or ''), str(args.get('etat') or ''), str(args.get('revue') or ''), args.get('recence') or None, str(args.get('q') or '')[:80],
                          str(args.get('curseur') or ''), args.get('limite') or 30, prefix=prefix)


def _versions(desk, row):
    return [{'version': r['version_number'], 'version_id': r['version_id'], 'state': liv.etat_contrat(dict(r)), 'review': r['review_state'], 'at': r['source_at']}
            for r in desk.db.execute('SELECT * FROM prepared_items5689 WHERE document_id=? ORDER BY version_number DESC LIMIT 30', (row['document_id'],))]


# ------------------------------------------------------------------------------------------------ pages
def _date(value):
    return '/'.join(reversed(str(value)[:10].split('-'))) if str(value)[:4].isdigit() else ''


def sources_html(sources, seules_alertes=False):
    """État de chaque source : une panne se voit, un vide reste un vide."""
    rows = [s for s in sources if not seules_alertes or s['state'] in ('unavailable', 'partial', 'stale')]
    if not rows:
        return ''
    if seules_alertes:
        return '<p class="l89-alerte" role="status"><strong>Liste incomplète.</strong> %s</p>' % ' '.join('%s : %s' % (e(s['label']), e(s['message'])) for s in rows[:4])
    return '<details class="l89-sources"><summary>État des sources</summary><ul>%s</ul></details>' % ''.join('<li><span>%s</span> <strong>%s</strong> <small>%s</small></li>' % (e(s['label']), e(SOURCE_STATE.get(s['state'], s['state'])), e(s['message'])) for s in rows)


def _ligne(c, retour):
    href = c['href'] + (('?' + urlencode({'retour': retour})) if retour else '')
    marks = [c['state_label'], c['review_label']]
    if c['exports']:
        marks.append('%d autre(s) format(s)' % c['exports'])
    if c['reserve']:
        marks.append('Réserves')
    if c.get('deposit_state'):
        marks.append(DEPOT.get(c['deposit_state'], ''))
    return '<li class="l89-ligne" data-livrable="%s"><a href="%s"><span class="l89-nature">%s</span><strong>%s</strong></a><span class="l89-meta">%s · version %d · %s</span><span class="l89-etats">%s</span></li>' % (
        e(c['document_id'], quote=True), e(href, quote=True), e(c['nature']), e(c['title']), e(c['matter_label']), c['version'], e(_date(c['updated_at'])), ''.join('<span class="l89-etat">%s</span>' % e(m) for m in marks if m))


def section_html(desk, prefix, matter=None, limite=8, retour='', titre='Documents préparés'):
    """Même projection partout : Produire et Dossier affichent cette section, Aujourd'hui la file qui lit le même registre. Une erreur de lecture ne casse jamais la page d'accueil de ces pages."""
    hidden, droits = liv.droits_requete()
    data = liv.projection(desk, hidden, droits, matter, recence=None, limite=limite, prefix=prefix)
    items = ''.join(_ligne(c, retour) for c in data['items'])
    alert = sources_html(data['sources'], True)
    empty = '<p class="ax-muted">Aucun document préparé pour l’instant.</p>' if data['complet'] else '<p class="ax-muted">Rien à afficher, mais une source n’a pas pu être lue : ce n’est pas un zéro certain.</p>'
    more = '%s/livrables%s' % (prefix, ('?' + urlencode({'dossier': matter})) if matter else '')
    return '<section class="ax-card l89-section" id="l89-section" data-revision="%s" data-scope="%s"><h2>%s <span class="q51-n">%d</span></h2>%s%s<p><a href="%s">Voir tous les documents préparés</a></p></section><script defer src="%s/static/v5689-livrables.js"></script>' % (
        e(data['revision'], quote=True), e(matter or '', quote=True), e(titre), data['counts']['total'], alert, ('<ul class="l89-liste">%s</ul>' % items) if items else empty, e(more, quote=True), e(prefix, quote=True))


def aujourdhui_html(desk, prefix):
    """Bandeau d'Aujourd'hui : liste partielle, ou documents anciens non relus. Rien à signaler : rien d'affiché."""
    hidden, droits = liv.droits_requete()
    data = liv.projection(desk, hidden, droits, limite=1, sync=False, prefix=prefix)
    parts = [sources_html(data['sources'], True)]
    old = data['counts']['a_relire_anciens']
    if old:
        parts.append('<p class="l89-note"><a href="%s">%d document(s) de plus de %d jours non relus</a> : ils restent accessibles.</p>' % (e(prefix + '/livrables?' + urlencode({'revue': 'to_review', 'recence': 'tout'}), quote=True), old, liv.RECENCE))
    return ''.join(parts)


def etat_documents_html(desk, prefix):
    """Filtre Documents d'Aujourd'hui : ce que la file ne montre pas, sans jamais laisser croire à un zéro certain."""
    hidden, droits = liv.droits_requete()
    data = liv.projection(desk, hidden, droits, limite=1, sync=False, prefix=prefix)
    running = liv.projection(desk, hidden, droits, etat='en_cours', limite=1, sync=False, prefix=prefix)['counts']['total']
    counts = data['counts']
    if not (counts['a_relire'] or counts['a_relire_anciens'] or running) and data['complet']:
        text = '<p class="l89-note">Aucun document à relire : le registre est vide de ce côté, toutes les sources ont répondu.</p>'
    else:
        text = '<p class="l89-note">À relire : %d · En cours : %d · Plus de %d jours : %d. <a href="%s">Tous les documents préparés</a></p>' % (counts['a_relire'], running, liv.RECENCE, counts['a_relire_anciens'], e(prefix + '/livrables', quote=True))
    return sources_html(data['sources'], True) + text


def _retour_sur(value, prefix):
    """Retour vers une adresse de l'application seulement : même contrôle que tous les détails (``ui_navigation5689``, § 14)."""
    from .ui_navigation5689 import valider_retour
    return valider_retour(str(value or ''), prefix) or prefix + '/livrables'


def _liste_page(desk, env, auth, prefix, args, shell):
    from .navigation5643 import production_tabs_html
    owner, role, hidden, droits = _contexte(desk, env)
    data = _projection(desk, args, hidden, droits, prefix)
    here = prefix + '/livrables' + (('?' + urlencode({k: v for k, v in args.items() if k in ('nature', 'etat', 'revue', 'recence', 'q', 'dossier', 'curseur') and v})) if any(args.get(k) for k in ('nature', 'etat', 'revue', 'recence', 'q', 'dossier', 'curseur')) else '')
    keep = {k: args[k] for k in ('etat', 'revue', 'recence', 'q', 'dossier') if args.get(k)}
    chips = ''.join('<a href="%s"%s>%s <span class="q51-n">%d</span></a>' % (e(prefix + '/livrables?' + urlencode({**keep, **({'nature': k} if k else {})}), quote=True), ' aria-current="true"' if str(args.get('nature') or '') == k else '', e(label), n)
                    for k, label, n in [('', 'Tous', data['counts']['total'])] + [(k, v, data['counts']['par_nature'][k]) for k, v in liv.NATURES.items()])
    recent = str(args.get('recence') or 'tout')
    options = ''.join('<option value="%s"%s>%s</option>' % (v, ' selected' if recent == v else '', l) for v, l in (('tout', 'Toute date'), ('7', '7 derniers jours'), ('30', '30 derniers jours'), ('90', '90 derniers jours')))
    reviews = ''.join('<option value="%s"%s>%s</option>' % (v, ' selected' if str(args.get('revue') or '') == v else '', e(l)) for v, l in (('', 'Toute relecture'), ('to_review', 'À relire'), ('reviewed', 'Relu'), ('changes_requested', 'Modifications demandées')))
    states = ''.join('<option value="%s"%s>%s</option>' % (v, ' selected' if str(args.get('etat') or '') == v else '', e(l)) for v, l in (('', 'Tout état'), ('pret', 'Prêt à relire'), ('en_cours', 'En cours'), ('echec', 'Échec ou incomplet'), ('archive', 'Archivé')))
    items = ''.join(_ligne(c, here) for c in data['items'])
    if not items:
        items = '<p class="ax-muted">%s</p>' % ('Aucun document pour ces filtres.' if data['complet'] else 'Rien à afficher, mais une source n’a pas pu être lue : ce n’est pas un zéro certain.')
    else:
        items = '<ul class="l89-liste">%s</ul>' % items
    pager = ''
    if data['next_cursor']:
        pager = '<p><a class="ax-btn ghost" href="%s">Page suivante</a></p>' % e(prefix + '/livrables?' + urlencode({**keep, **({'nature': args['nature']} if args.get('nature') else {}), 'curseur': data['next_cursor']}), quote=True)
    old = data['counts']['a_relire_anciens']
    note = ('<p class="l89-note">%d document(s) de plus de %d jours attendent une relecture.</p>' % (old, liv.RECENCE)) if old else ''
    body = ('%s<h1>Documents préparés</h1><p class="ax-muted">Tous les producteurs, sur un seul registre. Dernière lecture : %s.</p>%s%s'
            '<form class="l89-filtres" method="get" action="%s"><label>Recherche<input name="q" value="%s" maxlength="80"></label><label>Date<select name="recence">%s</select></label><label>Relecture<select name="revue">%s</select></label><label>État<select name="etat">%s</select></label>%s<button class="ax-btn ghost" type="submit">Filtrer</button></form>'
            '<nav class="l89-natures" aria-label="Filtrer par nature">%s</nav><div id="l89-liste" data-revision="%s" data-scope="%s">%s</div>%s%s<script defer src="%s/static/v5689-livrables.js"></script>') % (
        production_tabs_html(prefix, '/livrables'), e(_date(data['synced_at'] or '') or 'à venir'), sources_html(data['sources'], True), note, e(prefix + '/livrables', quote=True), e(str(args.get('q') or ''), quote=True), options, reviews, states,
        ('<input type="hidden" name="nature" value="%s">' % e(str(args['nature']), quote=True)) if args.get('nature') else '', chips, e(data['revision'], quote=True), e(str(args.get('dossier') or ''), quote=True), items, pager, sources_html(data['sources']), e(prefix, quote=True))
    return shell('Documents préparés', body, prefix, auth['csrf'], '/production')


def _bouton_retour(cible, prefix):
    from .ui_components5689 import icone
    from .ui_navigation5689 import libelle_source
    return icone('retour', taille=16) + '<span>' + e(libelle_source(cible, prefix)) + '</span>'


def _bouton_form(api_name, label, fields, ghost=True):
    return '<form class="m5-form m5-inline" data-api="%s" data-reload="1">%s<button class="ax-btn%s" type="submit">%s</button><p role="status" class="m5-status"></p></form>' % (
        e(api_name, quote=True), ''.join('<input type="hidden" name="%s" value="%s">' % (e(k, quote=True), e(str(v), quote=True)) for k, v in fields.items()), ' ghost' if ghost else '', e(label))


def _verification_html(check):
    groups = (('storage', 'Stockage'), ('format', 'Format et présentation'), ('sources', 'Sources et cohérence'), ('human_review', 'Relecture humaine'))
    out = []
    for domain, title in groups:
        lines = ''.join('<li><span>%s</span> <strong>%s</strong>%s</li>' % (e(c['label']), e(c['state_label']), ''.join('<br><small>%s</small>' % e(r) for r in c['reservations'][:6])) for c in check['checks'] if c['domain'] == domain)
        out.append('<h3>%s</h3><ul class="l89-controles">%s</ul>' % (e(title), lines))
    head = 'Prêt à relire : fichier accessible, contrôles prévus exécutés, limites ci-dessous.' if check['ready_for_review'] else 'Pas encore prêt à relire : des contrôles restent à faire.'
    return '<section class="ax-card" aria-labelledby="l89-t-ctrl"><h2 id="l89-t-ctrl">Contrôles de cette version</h2><p>%s</p>%s<p class="ax-muted">Ces contrôles ne valent pas validation juridique : seule votre relecture en décide.</p></section>' % (e(head), ''.join(out))


def _propositions_html(desk, row, droits):
    rows = patches.liste(desk, row['document_id'], 12)
    if not rows:
        return ''
    out = []
    for p in rows:
        compare = ''.join('<div class="l89-compare"><p><strong>Avant</strong></p><p class="l89-avant">%s</p><p><strong>Après</strong></p><p class="l89-apres">%s</p>%s</div>' % (e(c['avant'][:1500]), e(c['apres'][:1500]), ('<p class="ax-muted">Motif : %s</p>' % e(c['motif'])) if c['motif'] else '') for c in p['comparaison'])
        actions = ''
        if p['state'] in ('proposee',) and droits.get('valider'):
            base = {'version_id': p['base_version_id'], 'sha256': p['base_sha256']}
            api_name = 'm5689/documents/propositions/%s/decider' % p['id']
            actions = _bouton_form(api_name, 'Accepter', {**base, 'decision': 'accepter'}, False) + _bouton_form(api_name, 'Refuser', {**base, 'decision': 'refuser'})
        elif p['state'] == 'appliquee' and droits.get('valider') and p['result_version_id'] == row['version_id']:
            actions = _bouton_form('m5689/documents/propositions/%s/inverser' % p['id'], 'Proposer l’annulation', {'cle': uuid.uuid4().hex})
        conflict = ''
        if p['conflict'] and p['conflict'].get('choix'):
            conflict = '<p class="l89-alerte">Le document a changé : la proposition n’a pas été appliquée. %s</p>' % e(' · '.join(p['conflict']['choix']))
        out.append('<article class="l89-proposition"><h3>%s</h3><p class="ax-muted">%s%s</p>%s%s<div class="l89-actions">%s</div></article>' % (e(p['state_label']), 'Proposition de l’IA' if p['origin'] == 'ia' else 'Proposition', (' — ' + e(p['motif'][:200])) if p['motif'] else '', conflict, compare, actions))
    return '<section class="ax-card" aria-labelledby="l89-t-prop"><h2 id="l89-t-prop">Propositions de modification</h2><p class="ax-muted">%s</p>%s</section>' % (e(patches.NOTE_COMPARAISON), ''.join(out))


def _detail_page(desk, env, auth, prefix, ident, args, shell):
    owner, role, hidden, droits = _contexte(desk, env)
    version = str(args.get('version') or '')
    row = liv.charger(desk, ident, hidden, version_id=version or None, droits=droits)
    retour = _retour_sur(args.get('return_to') or args.get('retour'), prefix)
    labels, matters = liv._labels(desk)
    note, viewer = '', ''
    try:
        out = apercu.apercu(desk, row, prefix, version_demandee=version)
        row, note = out['row'], out['note']
        api_base = prefix + '/api440/m5689/livrables/' + row['document_id']
        query = '?version=' + e(row['version_id'], quote=True)
        if out['preview_kind'] == 'image' and out['url']:
            viewer = '<img class="l89-image" src="%s" alt="%s">' % (e(out['url'], quote=True), e('Aperçu : ' + row['title'], quote=True))
        elif out['url']:
            viewer = '<iframe class="l89-cadre" title="%s" src="%s"></iframe>' % (e('Aperçu : ' + row['title'], quote=True), e(out['url'], quote=True))
        elif out['html']:
            viewer = '<iframe class="l89-cadre" sandbox="" title="%s" src="%s"></iframe>' % (e('Aperçu : ' + row['title'], quote=True), api_base + '/cadre' + query)
    except Stop as ex:
        note = MESSAGES.get(str(ex).split(':')[0]) or 'Aperçu indisponible pour l’instant (%s). Le document reste au registre.' % str(ex).replace('_', ' ')
    view = apercu.vue(desk, row, owner, role)
    card = liv.carte(row, labels, matters, (), desk, prefix)
    check = verification.vue(desk, row)
    actions = view['actions']
    base = 'm5689/livrables/' + row['document_id']
    hidden_fields = {'version_id': row['version_id'], 'sha256': row['sha256']}
    from .ui_components5689 import action_ia_html
    bar = [action_ia_html('livrables', {'dossier': row['matter_id'], 'titre': row['title'], 'objets': [{'id': row['document_id'], 'version_id': row['version_id']}], 'page': True})]   # 5.6.89 : l'assistant reprend ce document et cette version
    if actions['ai_edit']:
        bar.append('<details class="l89-ia"><summary class="ax-btn">Modifier avec l’IA</summary><form class="m5-form" data-api="%s/modifier-ia" data-reload="1">%s<input type="hidden" name="cle" value="%s"><label>Votre consigne<textarea name="instruction" rows="3" maxlength="2000" required></textarea></label>'
                   '<label>Passage visé (facultatif)<input name="selection" maxlength="4000"></label><button class="ax-btn" type="submit">Demander une proposition</button><p role="status" class="m5-status"></p></form></details>' % (
                       e(base, quote=True), ''.join('<input type="hidden" name="%s" value="%s">' % (k, e(v, quote=True)) for k, v in hidden_fields.items()), uuid.uuid4().hex))
    if actions['editor']:
        from .fiche460 import doc_href
        bar.append('<a class="ax-btn ghost" href="%s">Ouvrir dans l’éditeur</a>' % e(prefix + doc_href(row['path'], row['matter_id']), quote=True))
    if actions['download']:
        bar.append('<a class="ax-btn ghost" href="%s">Télécharger</a>' % e('%s/api440/%s/telecharger?version=%s' % (prefix, base, row['version_id']), quote=True))
    if actions['export']:
        fmt = ''.join('<option value="%s">%s</option>' % (e(f, quote=True), e(f.upper())) for f in view['allowed_formats'])
        dest = ''.join('<option value="%s">%s</option>' % (e(k, quote=True), e(v)) for k, v in view['destinations'].items())
        bar.append('<details class="l89-export"><summary class="ax-btn ghost">Exporter</summary><form class="m5-form" data-api="%s/exporter" data-export="1"><input type="hidden" name="version_id" value="%s"><input type="hidden" name="cle" value="%s"><label>Format<select name="format">%s</select></label><label>Destination<select name="destination">%s</select></label>'
                   '<button class="ax-btn" type="submit">Lancer l’export</button><p role="status" class="m5-status"></p></form></details>' % (e(base, quote=True), e(row['version_id'], quote=True), uuid.uuid4().hex, fmt, dest))
    if row['protected'] or (row['path'] and not actions['ai_edit'] and row['lifecycle'] == 'current' and row['production_state'] in ('verified', 'published')):
        bar.append(_bouton_form(base + '/copie-travail', 'Créer une copie de travail', {'cle': uuid.uuid4().hex}))
    review = ''
    if row['lifecycle'] == 'current' and row['review_state'] != 'not_required' and row['production_state'] in ('verified', 'published'):
        fields = {'version_id': row['version_id']}
        review = '<div class="l89-actions">%s%s%s</div>' % (_bouton_form(base + '/relecture', 'Marquer relu', {**fields, 'decision': 'relu'}, False), _bouton_form(base + '/relecture', 'Demander des modifications', {**fields, 'decision': 'modifications'}), _bouton_form(base + '/relecture', 'Écarter', {**fields, 'decision': 'ecarte'}))
    if row['path'] and row['lifecycle'] == 'current':
        review += _bouton_form(base + '/controler', 'Relancer les contrôles', {})
    versions = _versions(desk, row)
    history = ''.join('<li>%s version %d — %s, %s (%s)</li>' % ('<strong>Affichée :</strong>' if v['version_id'] == row['version_id'] else '<a href="%s">Voir</a>' % e('%s/livrables/%s?%s' % (prefix, row['document_id'], urlencode({'version': v['version_id'], 'retour': retour})), quote=True), v['version'], e(liv.STATE[v['state']]), e(liv.REVIEW[v['review']]), e(_date(v['at']))) for v in versions)
    depot = ('<p>État de dépôt : <strong>%s</strong></p>' % e(DEPOT.get(row['deposit_state'], row['deposit_state']))) if row['deposit_state'] else ''
    reserve = ('<p class="l89-alerte"><strong>Réserves.</strong> %s</p>' % e(row['reserve_note'] or 'Document à compléter.')) if row['reserve'] else ''
    replaced = ('<p class="l89-alerte">Cette version a été remplacée. <a href="%s">Voir la dernière version</a></p>' % e(prefix + '/livrables/' + row['document_id'], quote=True)) if row['lifecycle'] == 'replaced' else ''
    limits = ''.join('<li>%s</li>' % e(r) for r in view['unavailable_reasons'])
    body = ('<article class="l89-detail" id="l89-detail" data-document="%s"><p><a class="ax-btn ghost ui89-retour" href="%s" data-l89-retour="1" data-ui89-retour>%s</a></p><header><p class="l89-nature">%s</p><h1>%s</h1><p class="ax-muted">%s · version %d · %s · %s</p><p><span class="l89-etat">%s</span> <span class="l89-etat">%s</span></p>%s%s%s</header>'
            '<div class="l89-actions" role="group" aria-label="Actions sur ce document">%s</div>%s<section class="ax-card" aria-labelledby="l89-t-apercu"><h2 id="l89-t-apercu">Aperçu</h2>%s%s</section>%s%s%s<section class="ax-card"><h2>Versions</h2><ul>%s</ul></section></article><script defer src="%s/static/v5689-livrables.js"></script>') % (
        e(row['document_id'], quote=True), e(retour, quote=True), _bouton_retour(retour, prefix), e(card['nature']), e(row['title']), e(card['matter_label']), row['version_number'], e(_date(card['updated_at'])), e(card['production_label'] or card['state_label']), e(card['state_label']), e(card['review_label']), depot, reserve, replaced,
        ''.join(bar), ('<ul class="l89-limites">%s</ul>' % limits) if limits else '', ('<p class="ax-muted">%s</p>' % e(note)) if note else '', viewer, review, _propositions_html(desk, row, droits), _verification_html(check), history, e(prefix, quote=True))
    return shell(row['title'], body, prefix, auth['csrf'], '/production')


def page_route(env, cfg, auth, prefix, path, args, shell):
    """Pages authentifiées : le contrôle d'accès du dossier est refait ici, avant tout rendu."""
    from .desk import Desk
    from .web440 import page_out
    desk = Desk(cfg)
    try:
        try:
            ident = path[len('/livrables/'):].strip('/') if path.startswith('/livrables/') else ''
            return page_out(_detail_page(desk, env, auth, prefix, ident, args, shell) if ident else _liste_page(desk, env, auth, prefix, args, shell))
        except Stop as ex:
            code = str(ex).split(':')[0]
            from .erreurs5634 import explain
            message = MESSAGES.get(code) or explain(code, getattr(ex, 'detail', None))
            html = shell('Document', '<p class="notice">%s</p><p><a class="ax-btn ghost" href="%s">Retour aux documents préparés</a></p>' % (e(message), e(prefix + '/livrables', quote=True)), prefix, auth['csrf'], '/production')
            out = page_out(html)
            out['status'] = STATUS.get(code, '400 Bad Request')
            return out
    finally:
        try:
            desk.db.close()
        except Exception:   # noqa: BLE001
            pass
