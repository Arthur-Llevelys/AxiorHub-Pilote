"""5.6.34 : parcours simplifiés et composants communs (chapitres 5, 6 et 7 du cahier des charges).

- Sélecteur de dossier (nom, référence, client, partie, n° RG, sans accents, paginé) et sélecteur de document (nom, date, état de
  lecture) : le serveur reçoit un identifiant de document et résout le chemin dans le dossier ; aucun chemin ni identifiant exact à saisir.
- Audiences : un seul espace, onglets Fiche (sans nouvel appel IA) et Préparer (plaidoirie : nos écritures, une ou plusieurs écritures
  adverses, pièces complémentaires) ; /audience et /audiences-word y conduisent en conservant le dossier.
- Produire › Réviser : le DOCX est choisi dans le dossier, l'original n'est jamais remplacé.
- Recherche juridique : question générale sans dossier ou recherche dans un dossier ; sources du registre unique ; résultats en cartes.
- Alertes du cabinet (anciennement Surveillance) : Marquer comme lu, Me le rappeler demain, Marquer comme traité, Rouvrir, Annuler la
  dernière action ; aucune échéance ni aucun agenda n'est modifié.
- Agenda local et profil du modèle local (panneaux insérés dans Agenda et Paramètres › IA).
"""
from datetime import datetime
from html import escape as e
import hashlib
import hmac
import json
from pathlib import PurePosixPath
import re
from urllib.parse import urlencode

from .common import Stop, fold, load_matters, matter_display

PAGE = 20


def _doc_id(mid, path):
    return hashlib.sha256(('%s|%s' % (mid, path)).encode('utf-8')).hexdigest()[:24]


# ------------------------------------------------------------------------------------------------ sélecteur de dossier
def _matter_terms(desk, m):
    terms = [m['id'], matter_display(m), m.get('client_name', ''), PurePosixPath(m.get('path', '')).name, *m.get('aliases', []), *m.get('references', [])]
    try:
        row = desk.db.execute('SELECT data FROM fiche5627 WHERE matter=? ORDER BY version DESC LIMIT 1', (m['id'],)).fetchone()
        if row:
            data = json.loads(row[0])
            terms += [p.get('nom', '') for p in data.get('parties', [])]
            terms.append((data.get('procedure') or {}).get('numero_rg', ''))
    except Exception:   # noqa: BLE001 — fiche absente
        pass
    return ' '.join(str(t) for t in terms if t)


def search_matters(desk, q='', page=1):
    """Recherche par nom, référence, client, partie et numéro RG ; accents ignorés ; résultats paginés."""
    words = [w for w in re.split(r'\s+', fold(q)) if w]
    rows = []
    for m in load_matters(desk.c):
        hay = fold(_matter_terms(desk, m))
        if all(w in hay for w in words):
            rows.append({'id': m['id'], 'label': matter_display(m), 'client': m.get('client_name', ''), 'status': m.get('state') or m.get('status') or ''})
    rows.sort(key=lambda r: fold(r['label']))
    page = max(1, int(page or 1))
    return {'total': len(rows), 'page': page, 'results': rows[(page - 1) * PAGE:page * PAGE]}


def matter_picker(desk, prefix, action, mid='', extra=None, label='Dossier'):
    """Composant « Dossier » : dossier courant (avec Changer) ou recherche ; fonctionne sans JavaScript (formulaire GET)."""
    extra = extra or {}
    hidden = ''.join('<input type="hidden" name="%s" value="%s">' % (e(k, quote=True), e(v, quote=True)) for k, v in extra.items() if v)
    current = next((m for m in load_matters(desk.c) if m['id'] == mid), None)
    if current:
        return ('<div class="m34-matter" data-m34-matter><p class="m34-current"><span>%s courant : <strong>%s</strong></span> '
                '<a class="ax-btn ghost" href="%s">Changer</a></p></div>') % (
            e(label), e(matter_display(current)), e(prefix + action + '?' + urlencode({k: v for k, v in extra.items() if v}), quote=True))
    return ('<form class="m34-matter" method="get" action="%s" data-m34-matter data-m34-target="%s" role="search">%s'
            '<label>%s<input type="search" name="q" autocomplete="off" maxlength="120" placeholder="Nom, référence, client, partie ou n° RG" '
            'data-m34-matter-q></label><button class="ax-btn" type="submit">Rechercher</button><ul class="m34-results" data-m34-results></ul></form>') % (
        e(prefix + action, quote=True), e(prefix + action, quote=True), hidden, e(label))


def matter_results_html(desk, prefix, action, q, extra=None):
    found = search_matters(desk, q)
    extra = {k: v for k, v in (extra or {}).items() if v}
    if not found['results']:
        return '<p class="m34-muted">Aucun dossier ne correspond à « %s ».</p>' % e(q)
    return '<ul class="m34-list">%s</ul>%s' % (''.join(
        '<li><a href="%s">%s</a> <small>%s</small></li>' % (e(prefix + action + '?' + urlencode({**extra, 'matter': r['id']}), quote=True), e(r['label']), e(r['client']))
        for r in found['results']), ('<p class="m34-muted">%d dossiers : précisez la recherche.</p>' % found['total']) if found['total'] > PAGE else '')


# ------------------------------------------------------------------------------------------------ sélecteur de document
def documents(desk, mid, q='', kinds=None):
    """Documents du dossier (index) : identifiant, version, nom, date fiable du fichier, état de lecture, sans exposer le chemin."""
    from . import synthese5632
    if not any(m['id'] == mid for m in load_matters(desk.c)):
        raise Stop('dossier_absent')
    words = [w for w in re.split(r'\s+', fold(q)) if w]
    states = synthese5632.states(desk, mid)
    out = []
    for d in synthese5632.inventory(desk, mid):
        name = PurePosixPath(d['path']).name
        if words and not all(w in fold(name) for w in words):
            continue
        if kinds and not name.lower().endswith(tuple(kinds)):
            continue
        rec = synthese5632.record_of(desk, mid, d['path'], d['sha256']) if d['state'] == 'extrait' else None
        reading = {'extrait': 'texte disponible', 'vide': 'sans texte', 'erreur': 'illisible', 'protegee': 'protégée', 'non_prise_en_charge': 'format non pris en charge'}[d['state']]
        if rec:
            reading = 'relevé complet' if rec['status'] == 'complet' else 'relevé incomplet'
        elif states.get(d['path'], {}).get('state') in ('retry_wait', 'blocked'):
            reading = 'relevé à reprendre'
        out.append({'document_id': _doc_id(mid, d['path']), 'version': d['etag'], 'name': name, 'date': d['modified'][:10], 'reading': reading,
                    'folder': PurePosixPath(d['path']).parent.name})
    out.sort(key=lambda x: x['date'], reverse=True)
    return out


UPLOAD_PREFIX = 'televerse:'


def uploaded_document(desk, mid, value, kinds=None):
    """Écrit téléversé depuis Audiences : accepté seulement si le dépôt vérifié a été tracé pour ce dossier."""
    import json
    path = str(value or '')[len(UPLOAD_PREFIX):]
    if kinds and not path.lower().endswith(tuple(kinds)):
        raise Stop('format_document_incorrect')
    for row in desk.db.execute("SELECT data FROM audit WHERE action='conclusions_televersees' ORDER BY id DESC LIMIT 500"):
        try:
            data = json.loads(row['data'] if hasattr(row, 'keys') else row[0])
        except (TypeError, ValueError):
            continue
        if data.get('path') == path and str(data.get('matter')) == str(mid):
            return path
    raise Stop('document_absent_du_dossier')


def resolve_document(desk, mid, document_id, version=None, kinds=None):
    """Le serveur résout le chemin : appartenance au dossier, format et version contrôlés."""
    from . import synthese5632
    if str(document_id or '').startswith(UPLOAD_PREFIX):
        return uploaded_document(desk, mid, document_id, kinds)
    for d in synthese5632.inventory(desk, mid):
        if _doc_id(mid, d['path']) == str(document_id or ''):
            if kinds and not d['path'].lower().endswith(tuple(kinds)):
                raise Stop('format_document_incorrect')
            if version and d['etag'] and str(version) != d['etag']:
                raise Stop('document_modifie_depuis_la_selection')
            return d['path']
    raise Stop('document_absent_du_dossier')


def document_picker(desk, mid, name, label, kinds=None, multiple=False):
    try:
        docs = documents(desk, mid, kinds=kinds)
    except Stop as ex:
        from .web440 import human
        return '<p class="m34-muted">%s</p>' % e(human(str(ex)))
    kind = 'checkbox' if multiple else 'radio'
    items = ''.join('<li data-m34-doc="%s"><label><input type="%s" name="%s" value="%s" data-version="%s" aria-label="%s"> <strong>%s</strong> <small>%s · %s · <span>%s</span></small></label></li>' % (
        e(fold(d['name'] + ' ' + d['folder']), quote=True), kind, e(name, quote=True), e(d['document_id'], quote=True), e(d['version'], quote=True),
        e('%s, %s, %s' % (d['name'], d['date'], d['reading']), quote=True), e(d['name']), e(d['date']), e(d['folder']), e(d['reading'])) for d in docs[:300])
    return ('<fieldset class="m34-docs" data-m34-docs><legend>%s</legend><input type="search" class="m34-filter" data-m34-filter placeholder="Filtrer par nom" '
            'aria-label="Filtrer les documents"><ul>%s</ul></fieldset>') % (e(label), items or '<li class="m34-muted">Aucun document indexé dans ce dossier.</li>')


# ------------------------------------------------------------------------------------------------ Audiences
def audiences_page(desk, auth, prefix, env, args, shell):
    from . import audience5613
    mid = str(args.get('matter') or '')
    tab = 'preparer' if args.get('onglet') == 'preparer' else 'fiche'
    body = '<h1>Audiences</h1><p class="m34-intro">La fiche réunit ce que l’on sait de la prochaine audience, sans nouvel appel à l’IA. La préparation de la plaidoirie est un travail lancé explicitement.</p>'
    body += '<nav class="m34-tabs" aria-label="Audiences">%s</nav>' % ''.join(
        '<a href="%s"%s>%s</a>' % (e(prefix + '/audiences?' + urlencode({'onglet': k, **({'matter': mid} if mid else {})}), quote=True),
                                    ' aria-current="page"' if k == tab else '', e(label)) for k, label in (('fiche', 'Fiche'), ('preparer', 'Préparer')))
    body += matter_picker(desk, prefix, '/audiences', mid, {'onglet': tab})
    if args.get('q') and not mid:
        body += matter_results_html(desk, prefix, '/audiences', args['q'], {'onglet': tab})
    if mid and tab == 'fiche':
        try:
            s = audience5613.sheet(desk, mid)
        except Stop as ex:
            from .web440 import human
            return shell('Audiences', body + '<p class="notice">%s</p>' % e(human(str(ex))), prefix, auth['csrf'], '/production')

        def block(title, items, render, empty):
            inner = ''.join('<li>%s</li>' % render(x) for x in items) if items else '<li class="m34-muted">%s</li>' % e(empty)
            return '<section class="ax-card"><h2>%s</h2><ul>%s</ul></section>' % (e(title), inner)
        body += '<p class="m34-muted">Données de l’agenda et de l’index%s ; éléments indisponibles signalés.</p>' % (
            (' (index du ' + e(s.get('snapshot_at', '').replace('T', ' ')) + ')') if s.get('snapshot_at') else '')
        body += block('Audience(s) à venir', s['hearings'], lambda x: '%s — %s%s' % (e(x['starts']), e(x['title']), (' · ' + e(x['location'])) if x['location'] else ''),
                      'Aucune audience enregistrée : aucune n’est inventée. Ajoutez-la dans l’Agenda ou préparez une audience provisoire.')
        body += block('Dernières écritures identifiées', s['conclusions'], lambda x: '%s <small>(%s · %s)</small>' % (e(x['name']), e(x['side']), e(x['modified'])),
                      'Aucun fichier « conclusions » dans le dossier.')
        body += block('Pièces et travaux attendus', s['missing_pieces'] + [{'title': t['title'] + (' — pour le ' + t['due'] if t['due'] else ''), 'detail': ''} for t in s['tasks']],
                      lambda x: e(x['title']) + (('<br><small>' + e(x['detail']) + '</small>') if x.get('detail') else ''), 'Rien d’attendu dans le registre.')
        body += block('Notes de plaidoirie produites', s['pleading_notes'], lambda x: '%s <small>%s</small>' % (e(x['label']), e(x['updated'])), 'Aucune note de plaidoirie vérifiée.')
        body += block('Questions probables', s['probable_questions'], lambda x: e(x['question']) + (('<br><small>Réponse proposée : ' + e(x['answer']) + '</small>') if x.get('answer') else ''),
                      'Disponibles après une préparation complète (onglet Préparer).')
        if s.get('unavailable'):
            body += '<p class="notice">Fiche partiellement indisponible : %s</p>' % e(' ; '.join(s['unavailable']))
        body += '<p><a class="ax-btn" href="%s">Préparer la plaidoirie</a></p>' % e(prefix + '/audiences?' + urlencode({'onglet': 'preparer', 'matter': mid}), quote=True)
    elif mid and tab == 'preparer':
        body += ('<form class="m34-form" data-m34-form="audience/preparer" data-matter="%s"><label>Objectif, date de l’audience et durée de parole'
                 '<textarea name="instruction" required maxlength="5000" rows="4" placeholder="Audience du …, 15 minutes, obtenir le rejet de la demande de …"></textarea></label>%s%s%s'
                 '<p class="m34-help">Sans écritures adverses, l’analyse est provisoire et le signale. Plusieurs adversaires : une préparation est lancée pour chacune '
                 'de leurs écritures, regroupées ici. Rien n’est déposé ni envoyé.</p><button class="ax-btn" type="submit">Préparer la plaidoirie</button>'
                 '<p role="status" class="m34-status"></p></form>') % (
            e(mid, quote=True), document_picker(desk, mid, 'ours', 'Nos dernières écritures', ('.docx', '.pdf')),
            document_picker(desk, mid, 'opponents', 'Écritures adverses (une ou plusieurs)', ('.docx', '.pdf'), multiple=True),
            document_picker(desk, mid, 'extra', 'Pièces complémentaires (facultatif)', None, multiple=True))
        # U01 : écritures reçues hors du dossier (courriel, clé USB) ; dépôt sans remplacement dans 60_Audiences/90_AxiorHub_Sources.
        body += ('<section class="ax-card m34-upload" data-m34-upload-matter="%s"><h2>Écritures absentes du dossier</h2>'
                 '<p class="m34-help">Fichier DOCX ou PDF de 20 Mo maximum. Il est déposé dans le dossier (60_Audiences), vérifié, puis coché ci-dessus. '
                 'Aucun fichier existant n’est remplacé.</p>'
                 '<label>Téléverser nos écritures<input type="file" accept=".docx,.pdf" data-m34-upload="ours"></label>'
                 '<p role="status" class="m34-status" data-m34-upload-status="ours"></p>'
                 '<label>Téléverser des écritures adverses<input type="file" accept=".docx,.pdf" data-m34-upload="opponent"></label>'
                 '<p role="status" class="m34-status" data-m34-upload-status="opponent"></p></section>') % e(mid, quote=True)
        try:
            rows = desk.db.execute('SELECT id,status,created FROM hearing_projects_v250 WHERE matter=? ORDER BY created DESC LIMIT 10', (mid,)).fetchall()
        except Exception:   # noqa: BLE001
            rows = []
        body += '<section class="ax-card"><h2>Préparations de ce dossier</h2><ul>%s</ul></section>' % (''.join(
            '<li>%s · %s · <a href="%s">Ouvrir le résultat</a></li>' % (e(str(r['created'])[:16].replace('T', ' ')), e(r['status']),
                                                                         e(prefix + '/audiences-word?' + urlencode({'hearing': r['id']}), quote=True)) for r in rows)
            or '<li class="m34-muted">Aucune préparation pour ce dossier.</li>')
    return shell('Audiences', body, prefix, auth['csrf'], '/production')


def prepare_hearing(desk, data):
    mid = str(data.get('matter') or '')
    instruction = str(data.get('instruction') or '').strip()
    if not 5 <= len(instruction) <= 5000:
        raise Stop('instruction_audience_invalide')
    ours = resolve_document(desk, mid, data.get('ours'), data.get('ours_version'), ('.docx', '.pdf')) if data.get('ours') else ''
    if not ours:
        raise Stop('nos_ecritures_requises')
    opponents = [resolve_document(desk, mid, x, None, ('.docx', '.pdf')) for x in (data.get('opponents') or [])][:6]
    extra = [PurePosixPath(resolve_document(desk, mid, x)).name for x in (data.get('extra') or [])][:12]
    if extra:
        instruction += '\nPièces complémentaires à emporter et à exploiter : ' + ', '.join(extra)
    jobs = []
    for opp in (opponents or ['']):
        jobs.append(desk.enqueue('prepare_hearing', {'matter': mid, 'instruction': instruction[:5000], 'our_source_path': ours, 'opponent_source_path': opp}))
    desk.audit('audience5634_preparation', {'matter': mid, 'jobs': jobs, 'adversaires': len(opponents)})
    return {'jobs': jobs, 'message': ('Préparation lancée%s.' % ((' pour %d écritures adverses' % len(opponents)) if len(opponents) > 1 else '')) +
            ('' if opponents else ' Analyse provisoire : aucune écriture adverse sélectionnée.')}


# ------------------------------------------------------------------------------------------------ Produire › Réviser
def reviser_page(desk, auth, prefix, env, args, shell):
    mid = str(args.get('matter') or '')
    body = ('<h1>Réviser un document existant</h1><p class="m34-intro">Choisissez le dossier puis le document Word ; expliquez les modifications. '
            'Le document d’origine n’est jamais remplacé : la version révisée est créée à côté, avec la comparaison et les points de contrôle.</p>')
    body += matter_picker(desk, prefix, '/reviser', mid)
    if args.get('q') and not mid:
        body += matter_results_html(desk, prefix, '/reviser', args['q'])
    if mid:
        body += ('<form class="m34-form" data-m34-form="reviser" data-matter="%s">%s<label>Modifications souhaitées<textarea name="instruction" required '
                 'maxlength="5000" rows="5" placeholder="Actualiser les demandes au vu des dernières pièces adverses ; reprendre le dispositif."></textarea></label>'
                 '<button class="ax-btn" type="submit">Lancer la révision</button><p role="status" class="m34-status"></p></form>') % (
            e(mid, quote=True), document_picker(desk, mid, 'document', 'Document Word à réviser', ('.docx',)))
        try:
            rows = desk.db.execute("SELECT id,request,status,path,updated FROM docreq520 WHERE matter=? AND revision_of<>'' ORDER BY updated DESC LIMIT 10", (mid,)).fetchall()
        except Exception:   # noqa: BLE001
            rows = []
        from .decisions5634 import DOC_STATUS, detail_url
        body += '<section class="ax-card"><h2>Révisions de ce dossier</h2><ul>%s</ul></section>' % (''.join(
            '<li><strong>%s</strong> · %s · <a href="%s">Détail</a></li>' % (e(r['request'][:120]), e(DOC_STATUS.get(r['status'], r['status'])),
                                                                         e(detail_url(prefix, 'docreq', r['id']), quote=True)) for r in rows)
            or '<li class="m34-muted">Aucune révision demandée.</li>')
    body += '<p><a href="%s">Autres modes de Produire : Rédiger, Bordereau, Mission complète</a></p>' % e(prefix + '/production', quote=True)
    return shell('Réviser un document', body, prefix, auth['csrf'], '/production')


def submit_revision(desk, data):
    from . import docrequest520
    mid = str(data.get('matter') or '')
    path = resolve_document(desk, mid, data.get('document'), data.get('version'), ('.docx',))
    out = docrequest520.submit(desk, str(data.get('instruction') or ''), mid, 'auto', revision_path=path)
    return {**out, 'message': 'Révision lancée : la version révisée sera créée à côté de l’original, qui reste intact.'}


# ------------------------------------------------------------------------------------------------ Recherche juridique
def research_page(desk, auth, prefix, env, args, shell):
    from . import registre5634
    mid = str(args.get('matter') or '')
    sources = registre5634.status(desk)
    active = [s for s in sources if s['active']]
    body = ('<h1>Recherche juridique</h1><p class="m34-intro">Une question générale fonctionne sans dossier. La question est anonymisée avant '
            'tout envoi. Les résultats distinguent textes, décisions et pistes ; seule une décision revérifiée sur la source officielle est citable.</p>')
    body += matter_picker(desk, prefix, '/recherche-juridique', mid, label='Dossier (facultatif)')
    if args.get('q') and not mid:
        body += matter_results_html(desk, prefix, '/recherche-juridique', args['q'])
    checks = ''.join('<label class="m34-check"><input type="checkbox" name="providers" value="%s" checked> %s <small>%s</small></label>' % (
        e(s['id'], quote=True), e(s['name']), e(', '.join(registre5634.CAPABILITY_LABELS.get(c, c) for c in s['capabilities']) + ((' — ' + s['note']) if s['note'] else ''))) for s in active)
    missing = ''.join('<li>%s : %s</li>' % (e(s['name']), e(s['reason'])) for s in sources if not s['active'])
    body += ('<form class="m34-form" data-m34-form="recherche" data-matter="%s"><label>Question<textarea name="question" required maxlength="3000" rows="4"></textarea></label>'
             '<div class="m34-row"><label>Domaine ou procédure<input name="domaine" maxlength="120"></label><label>Territoire<select name="territoire">'
             '<option value="FR">France</option><option value="UE">Union européenne</option><option value="CEDH">CEDH</option><option value="INT">Autre</option></select></label>'
             '<label>Date d’application<input type="date" name="date"></label></div><details class="m34-sources"%s><summary>Sources (%d actives)</summary>%s%s</details>'
             '<button class="ax-btn" type="submit"%s>Rechercher</button><p role="status" class="m34-status"></p></form>') % (
        e(mid, quote=True), ' open' if not active else '', len(active), checks or '<p class="m34-muted">Aucune source juridique active.</p>',
        ('<ul class="m34-muted">%s</ul><p><a href="%s">Configurer les sources</a></p>' % (missing, e(prefix + '/sources', quote=True))) if missing else '',
        '' if active else ' disabled')
    body += results_html(desk, prefix, mid)
    if mid:
        body += ('<p class="m34-muted">Les commandes « Identifier les dernières écritures » et « Actualiser le registre des pièces » se trouvent désormais dans '
                 '<a href="%s">l’onglet Pièces du dossier</a>.</p>') % e(prefix + '/dossier?' + urlencode({'id': mid, 'vue': 'pieces'}), quote=True)
    return shell('Recherche juridique', body, prefix, auth['csrf'], '/recherche')


def results_html(desk, prefix, mid=''):
    rows = desk.db.execute("SELECT id,args,status,result,finished FROM jobs WHERE kind='recherche5634' ORDER BY id DESC LIMIT 30").fetchall()
    cards = []
    for r in rows:
        try:
            a = json.loads(r['args'] or '{}')
            res = json.loads(r['result'] or '{}') or {}
        except ValueError:
            continue
        if mid and a.get('matter') != mid:
            continue
        head = '<header><strong>%s</strong> <small>%s · %s</small></header>' % (e(str(a.get('question', ''))[:200]), e(str(r['finished'] or '')[:16].replace('T', ' ')),
                                                                            e({'done': 'terminée', 'error': 'échec', 'pending': 'en file', 'running': 'en cours'}.get(r['status'], r['status'])))
        verified = {str(v.get('identifier') or v.get('ecli') or '') for v in res.get('verified_authorities', [])}
        items = ''
        for lead in res.get('leads', [])[:20]:
            ident = str(lead.get('identifier') or lead.get('ecli') or '')
            ok = ident and ident in verified
            items += ('<li class="m34-lead"><span class="m34-badge %s">%s</span> <strong>%s</strong><br><small>%s · %s · %s · source : %s</small>%s%s</li>') % (
                'ok' if ok else 'piste', 'vérifiée sur la source officielle' if ok else 'piste à vérifier', e(lead.get('title') or ident or 'Résultat'),
                e(lead.get('court', '')), e(lead.get('date', '')), e(ident), e(lead.get('provider', '')),
                ('<blockquote>%s</blockquote>' % e(str(lead.get('exact_excerpt') or lead.get('summary') or '')[:600])) if (lead.get('exact_excerpt') or lead.get('summary')) else '',
                (' <a href="%s" target="_blank" rel="noopener noreferrer">Ouvrir la source</a>' % e(lead.get('official_url') or lead.get('provider_url'), quote=True))
                if str(lead.get('official_url') or lead.get('provider_url') or '').startswith('https://') else '')
        reports = ''.join('<li>%s : %s</li>' % (e(x.get('provider') or x.get('connector') or ''), e(x.get('error') or x.get('status') or ''))
                          for x in res.get('reports', []) if x.get('status') not in ('ok', 'completed'))
        err = ''
        if r['status'] == 'error':
            from .erreurs5634 import explain
            err = '<p class="notice">%s</p>' % e(explain(res.get('erreur', 'erreur'), res.get('detail')))
        cards.append('<article class="ax-card m34-research">%s%s<ul>%s</ul>%s<details><summary>Détail technique</summary><pre>%s</pre></details></article>' % (
            head, err, items or '<li class="m34-muted">Aucun résultat.</li>', ('<p class="m34-muted">Sources en échec (les autres résultats restent affichés) :</p><ul>%s</ul>' % reports) if reports else '',
            e(json.dumps({'requete_anonymisee': res.get('anonymized_query', ''), 'rapports': res.get('reports', [])}, ensure_ascii=False, indent=1)[:4000])))
    return '<section><h2>Recherches récentes</h2>%s</section>' % (''.join(cards) or '<p class="m34-muted">Aucune recherche.</p>')


def research_job(desk, args):
    from . import registre5634
    question = str(args.get('question') or '').strip()
    extra = ' '.join(x for x in (('Domaine : ' + args['domaine']) if args.get('domaine') else '', ('Territoire : ' + args['territoire']) if args.get('territoire') else '',
                                 ('Droit applicable à la date du ' + args['date']) if args.get('date') else '') if x)
    return registre5634.search(desk, (question + ('\n' + extra if extra else ''))[:3000], str(args.get('matter') or ''), args.get('providers') or None)


# ------------------------------------------------------------------------------------------------ Alertes du cabinet
ALERT_INTRO = ('Retrouvez les changements et points qui demandent une attention : nouvelles pièces, échéances, messages sans suite ou éléments à '
               'vérifier. Les actions ci-dessous organisent ces alertes ; elles ne modifient pas vos échéances de procédure.')
TECH = ('connexion', 'technique', 'imap', 'nextcloud', 'service')


def alerts_html(desk, prefix, selected='open', csrf=''):
    from .proactive import history5634, signals
    rows = signals(desk, selected, limit=200)
    labels = {m['id']: matter_display(m) for m in load_matters(desk.c)}
    tabs = (('open', 'À traiter'), ('acknowledged', 'Lues'), ('snoozed', 'Rappel programmé'), ('resolved', 'Traitées'), ('all', 'Toutes'))
    forms = ''.join('<form method="post" action="%s/action"><input type="hidden" name="csrf" value="%s"><input type="hidden" name="action" value="%s">'
                    '<button class="ax-btn ghost">%s</button></form>' % (e(prefix, quote=True), e(csrf, quote=True), a, e(label))
                    for a, label in (('monitor_all', 'Actualiser les alertes de tous les dossiers'), ('build_daily_dashboard', 'Recalculer les priorités')))
    out = '<section class="daily-hero"><div><h2>Alertes du cabinet</h2><p>%s</p></div><div class="actions">%s</div></section>' % (e(ALERT_INTRO), forms)
    out += '<nav class="tabs">%s</nav>' % ''.join('<a class="%s" href="%s">%s</a>' % ('active' if selected == k else '', e(prefix + '/surveillance?' + urlencode({'state': k}), quote=True), e(v))
                                                for k, v in tabs)
    business, technical = [], []
    for s in rows:
        hist = history5634(desk, s['id'], 3)
        last = hist[0] if hist else None
        proof = ''
        if s['state'] == 'resolved':
            proof = ('Traité par %s le %s' % (last['actor'], last['at'][:10])) if last and last['actor'] != 'systeme' else 'Condition disparue après contrôle'
        elif s['state'] == 'acknowledged' and last:
            proof = 'Lu par %s le %s' % (last['actor'], last['at'][:10])
        elif s['state'] == 'snoozed':
            proof = 'Rappel programmé le %s' % str(s.get('snoozed_until') or '')[:16].replace('T', ' ')
        primary = '<a class="ax-btn" href="%s">%s</a>' % (e(prefix + '/dossier?' + urlencode({'id': s['matter']}), quote=True),
                                                         'Examiner l’échéance' if s.get('due') else 'Ouvrir le dossier')
        actions = ''
        if s['state'] in ('open',):
            actions += ('<button type="button" class="ax-btn ghost" data-m34-alert="read" title="L’alerte a été prise en connaissance ; le point reste à traiter">Marquer comme lu</button>'
                        '<button type="button" class="ax-btn ghost" data-m34-alert="remind" title="Masque l’alerte jusqu’à demain ; ne change ni l’agenda ni la date limite">Me le rappeler demain</button>')
        if s['state'] != 'resolved':
            actions += '<button type="button" class="ax-btn ghost" data-m34-alert="done" title="Classe l’alerte sur votre décision ; n’exécute pas l’action proposée">Marquer comme traité</button>'
        else:
            actions += '<button type="button" class="ax-btn ghost" data-m34-alert="reopen">Rouvrir</button>'
        if last and last['actor'] != 'systeme' and last['note'] != 'annulation':
            actions += '<button type="button" class="ax-btn ghost" data-m34-alert="undo">Annuler la dernière action</button>'
        urgency = {'critical': 'critique', 'high': 'élevée', 'normal': 'normale', 'low': 'faible'}.get(s['severity'], s['severity'])
        card = ('<article class="signal %s" data-m34-signal="%s"><div><small>%s · <span>urgence %s</span>%s · <span>détectée le</span> %s</small><h3>%s</h3><p>%s</p>'
                '<p><strong>Action utile :</strong> %s</p>%s%s</div><div class="signal-actions">%s%s<p role="status" class="m34-status"></p></div></article>') % (
            e(s['severity']), e(s['id'], quote=True), e(labels.get(s['matter'], s['matter'])), e(urgency), (' · <span>échéance</span> ' + e(s['due'][:10])) if s.get('due') else '',
            e(str(s.get('first_seen') or s.get('last_seen') or '')[:10]), e(s['title']), e(s['detail']), e(s['proposed_action']),
            ('<p class="m34-muted">%s</p>' % e(proof)) if proof else '',
            ('<details><summary>Sources</summary><ul>%s</ul></details>' % ''.join('<li>%s</li>' % e(x) for x in s['source_ids'][:20])) if s['source_ids'] else '',
            primary, actions)
        (technical if any(t in fold(s['category']) for t in TECH) else business).append(card)
    out += ''.join(business) or '<p class="empty">Aucune alerte dans cette rubrique.</p>'
    if technical:
        out += '<section><h2>Incidents techniques de connexion</h2>%s</section>' % ''.join(technical)
    return out


# ------------------------------------------------------------------------------------------------ Agenda local
def _local(desk, ev):
    """Date et heure dans le fuseau du cabinet (date seule pour un événement sur la journée)."""
    from datetime import datetime
    from .agenda5634 import _tz
    when = datetime.fromisoformat(ev['starts']).astimezone(_tz(desk))
    return when.strftime('%d/%m/%Y') if ev.get('all_day') else when.strftime('%d/%m/%Y %H:%M')


def agenda_panel(desk, prefix):
    from . import agenda5634
    agenda5634.schema(desk)
    m = agenda5634.mode(desk)
    kinds = ''.join('<option value="%s">%s</option>' % (k, e(v)) for k, v in agenda5634.KINDS.items())
    reminders = ''.join('<label class="m34-check"><input type="checkbox" name="reminders" value="%d"%s> %s</label>' % (
        n, ' checked' if n in (60, 1440) else '', e({0: 'à l’heure', 10: '10 min', 30: '30 min', 60: '1 h', 120: '2 h', 1440: '1 jour', 2880: '2 jours', 10080: '1 semaine'}[n]))
        for n in agenda5634.REMINDERS)
    matters = ''.join('<option value="%s">%s</option>' % (e(x['id'], quote=True), e(matter_display(x))) for x in sorted(load_matters(desk.c), key=lambda x: fold(matter_display(x))))
    upcoming = [x for x in agenda5634.listing(desk) if x.get('calendar_scope') != 'legacy_bridge']   # 5.6.43 : compteur personnel seul
    legacy = agenda5634.legacy_inventory(desk)
    labels = {x['id']: matter_display(x) for x in load_matters(desk.c)}
    tz = agenda5634._tz(desk)

    def edit_form(ev):   # 5.6.35 : modification partielle (seuls les champs du formulaire), contrôle de version contre l'écrasement
        a, b = datetime.fromisoformat(ev['starts']).astimezone(tz), datetime.fromisoformat(ev['ends']).astimezone(tz)
        when = ('<label>Jour<input type="date" name="start" value="%s" required></label>' % a.strftime('%Y-%m-%d')) if ev['all_day'] else (
            '<label>Début<input type="datetime-local" name="start" value="%s" required></label><label>Durée (minutes)<input type="number" '
            'name="duration_minutes" min="5" max="1440" step="5" value="%d"></label>') % (a.strftime('%Y-%m-%dT%H:%M'), int((b - a).total_seconds() // 60))
        return ('<details class="m34-edit"><summary>Modifier</summary><form class="m34-form" data-m34-form="agenda/modifier">'
                '<input type="hidden" name="id" value="%s"><input type="hidden" name="base" value="%s">'
                '<div class="m34-row"><label>Intitulé<input name="title" value="%s" required maxlength="200"></label>%s</div>'
                '<div class="m34-row"><label>Lieu<input name="location" value="%s" maxlength="200"></label></div>'
                '<label>Notes<textarea name="description" rows="2" maxlength="2000">%s</textarea></label>'
                '<button class="ax-btn" type="submit">Enregistrer la modification</button><p role="status" class="m34-status"></p></form></details>') % (
            e(ev['id'], quote=True), e(ev['updated'], quote=True), e(ev['title'], quote=True), when, e(ev['location'], quote=True), e(ev['description']))

    rows = ''.join('<li data-m34-event="%s" data-m34-matter="%s" data-m34-text="%s"><strong>%s</strong> · %s <small>%s%s%s</small> '
                   '<button type="button" class="ax-btn ghost" data-m34-agenda-cancel '
                   'data-confirm="Annuler cet événement ? Il reste dans l’historique et les sauvegardes.">Annuler</button>%s%s</li>' % (
                       e(ev['id'], quote=True), e(ev['matter'] or '', quote=True),
                       e(' '.join((ev['title'], ev['location'] or '', labels.get(ev['matter'], ev['matter']) or '')).casefold(), quote=True),
                       e(ev['title']), e(ev['kind_label']), e(_local(desk, ev)),
                       (' · dossier ' + e(labels.get(ev['matter'], ev['matter']))) if ev['matter'] else '',
                       {'a_synchroniser': ' · ancienne copie distante à reprendre', 'synchronise': ' · ancienne copie distante conservée'}.get(ev['sync_state'], ''),
                       (' <button type="button" class="ax-btn ghost" data-m34-agenda-cancel data-m34-scope="serie" data-confirm="Annuler toutes '
                        'les occurrences à venir de cette série ?">Annuler la série</button>') if ev['series'] else '', edit_form(ev))
                   for ev in upcoming[:200])
    saved = agenda5634.backups(desk)[:10]
    restore = ''.join('<li>%s%s <button type="button" class="ax-btn ghost" data-m34-agenda-restore="%s">Restaurer…</button></li>' % (
        e(b['name']), '' if b['exacte'] else ' <small>(import seulement : statuts et rappels non rétablis)</small>', e(b['name'], quote=True)) for b in saved)
    legacy_html = ''
    if legacy:   # 5.6.43 (§ 17.7) : décision explicite, aucune copie ni suppression automatique
        legacy_html = ('<details class="m34-legacy"><summary>Anciens événements copiés vers Nextcloud avant 5.6.43 : %d à répartir</summary>'
                       '<p>Ces événements avaient une copie (ou une copie en attente) dans le calendrier Nextcloud de planification. Choisissez pour '
                       'chacun : le garder seulement dans l’Agenda personnel (la copie déjà créée n’est pas supprimée) ou continuer à tenir sa copie '
                       'distante à jour. Aucune copie en attente ne repart sans votre choix.</p><ul>' % len(legacy) + ''.join(
                           '<li><strong>%s</strong> · %s %s%s</li>' % (
                               e(ev['title']), e(_local(desk, ev)),
                               ''.join('<form class="m34-form" data-m34-form="agenda/repartir"><input type="hidden" name="id" value="%s">'
                                       '<input type="hidden" name="decision" value="%s"><button class="ax-btn ghost" type="submit">%s</button></form>' % (
                                           e(ev['id'], quote=True), d, label)
                                       for d, label in (('garder_local', 'Garder dans l’Agenda personnel'), ('garder_copie', 'Garder la copie distante'))),
                               '<p role="status" class="m34-status"></p>') for ev in legacy) + '</ul></details>')
    return ('<section class="ax-card m34-agenda" data-m34-agenda><h2>Agenda personnel</h2><p class="m34-mode"><strong>%s</strong> — %s</p>'
            '<details%s><summary>Ajouter un rendez-vous, une audience ou une échéance</summary><form class="m34-form" data-m34-form="agenda/creer">'
            '<div class="m34-row"><label>Type<select name="kind">%s</select></label><label>Intitulé<input name="title" required maxlength="200"></label></div>'
            '<div class="m34-row"><label>Début<input type="datetime-local" name="start" required></label><label>Durée (minutes)<input type="number" name="duration_minutes" '
            'min="5" max="1440" step="5" value="60"></label><label class="m34-check"><input type="checkbox" name="all_day" value="1"> Toute la journée</label></div>'
            '<div class="m34-row"><label>Dossier<select name="matter"><option value="">Sans dossier</option>%s</select></label><label>Lieu<input name="location" maxlength="200"></label></div>'
            '<fieldset class="m34-reminders"><legend>Rappels</legend>%s</fieldset><label>Notes<textarea name="notes" rows="2" maxlength="2000"></textarea></label>'
            '<button class="ax-btn" type="submit">Enregistrer</button><p role="status" class="m34-status"></p></form></details>'
            '%s<h3>Prochains événements de l’Agenda personnel</h3><div class="m34-row m34-filter"><label>Rechercher<input type="search" '
            'data-m34-agenda-search placeholder="Intitulé, lieu ou dossier"></label><label>Dossier<select data-m34-agenda-matter><option value="">'
            'Tous les dossiers</option>%s</select></label></div><ul class="m34-list" data-m34-agenda-list>%s</ul>'
            '<p class="m34-muted" data-m34-agenda-none hidden>Aucun événement ne correspond à ce filtre.</p>'
            '<details><summary>Importer, exporter, sauvegarder</summary><form class="m34-form" data-m34-form="agenda/importer" data-m34-ics>'
            '<label>Fichier iCalendar (.ics : export Nextcloud, Google Agenda, Outlook)<input type="file" name="ics" accept=".ics,text/calendar"></label>'
            '<label>ou adresse iCal HTTPS (adresse secrète Google, lien d’abonnement Nextcloud ; elle n’est pas conservée)<input type="url" name="url" placeholder="https://…"></label>'
            '<button class="ax-btn" type="submit">Importer</button><p role="status" class="m34-status"></p></form>'
            '<p><button type="button" class="ax-btn ghost" data-m34-agenda-export>Exporter (.ics)</button> <button type="button" class="ax-btn ghost" data-m34-agenda-backup>'
            'Sauvegarder maintenant</button></p><p class="m34-muted">Une sauvegarde est faite chaque jour automatiquement (30 jours conservés). '
            'Importer ajoute des événements ; restaurer rétablit l’agenda tel qu’il était (aperçu avant toute modification, état actuel sauvegardé '
            'au préalable).</p><h3>Sauvegardes</h3><ul class="m34-list">%s</ul><p role="status" class="m34-status" data-m34-restore-status></p></details></section>') % (
        e(m['label']), e(m['help']), ' open' if not upcoming else '', kinds, matters, reminders, legacy_html, matters,
        rows or '<li class="m34-muted">Aucun événement à venir dans l’Agenda personnel.</li>', restore or '<li class="m34-muted">Aucune sauvegarde pour l’instant.</li>')


# ------------------------------------------------------------------------------------------------ Profil du modèle local
def profile_panel(desk, prefix):
    from . import profil5634
    prof = profil5634.current(desk.c)
    ollama = desk.c.get('ollama') or {}
    routing = desk.c.get('model_routing') or {}
    values = prof['values'] or {}
    last = profil5634.last_values(desk.c.get('state_dir', ''))
    test = desk.settings('llm5634:last_test', None)
    adopt = ''
    if prof['profile'] == 'herite':
        adopt = ('<div class="m34-notice"><p>Réglages conservés de la version précédente : température %s. Le profil <strong>Juridique</strong> (température 0,3, '
                 'top_p 0,9, top_k 40) est proposé ; rien n’est changé sans votre accord.</p><button type="button" class="ax-btn" data-m34-profile="adopter">'
                 'Adopter le profil Juridique</button></div>') % e(', '.join('%s %s' % (k, str(v).replace('.', ',')) for k, v in prof['legacy_temperatures'].items()))

    def field(key, step):
        v = values.get(key, profil5634.PROFILES['juridique'][key])
        return '<label>%s<input name="%s" value="%s" inputmode="decimal" data-step="%s"><small>%s</small></label>' % (
            e(key), e(key, quote=True), e(str(v).replace('.', ',')), step, e(profil5634.HELP[key]))
    last_rows = ''.join('<li><strong>%s</strong> : %s (%s, %s)%s</li>' % (e(k), e(', '.join('%s=%s' % (a, b) for a, b in v.get('options', {}).items())),
                                                                       e(v.get('profile', '')), e(v.get('at', '')[:16].replace('T', ' ')),
                                                                       (' — non transmis : ' + e(', '.join(v['ignored']))) if v.get('ignored') else '') for k, v in sorted(last.items())[:12])
    return ('<section class="ax-card m34-profile" data-m34-profile-panel><h2>Modèles locaux</h2><dl class="m34-dl"><dt>Serveur</dt><dd>%s</dd>'
            '<dt>Modèle rapide</dt><dd>%s</dd><dt>Modèle approfondi</dt><dd>%s</dd><dt>Profil</dt><dd><strong>%s</strong>%s</dd></dl>%s'
            '<p><button type="button" class="ax-btn" data-m34-profile="tester">Tester</button> <button type="button" class="ax-btn ghost" data-m34-profile="restaurer">'
            'Restaurer le profil juridique</button></p><p role="status" class="m34-status"></p>%s'
            '<details><summary>Réglages avancés</summary><p>%s</p><form class="m34-form" data-m34-form="modeles/profil">%s%s%s%s%s%s'
            '<label>Fenêtre de contexte<select name="context_mode"><option value="auto"%s>Automatique (32 768 au plus, selon le modèle)</option>'
            '<option value="fixe"%s>Valeur fixée dans Connexions (%s)</option></select><small>%s</small></label>'
            '<button class="ax-btn" type="submit">Enregistrer un profil personnalisé</button><p role="status" class="m34-status"></p></form>'
            '<p class="m34-muted">Les relevés de pièces et contrôles factuels utilisent le sous-profil « Extraction factuelle » (température 0), annoncé dans '
            'le détail des travaux. Ces réglages ne remplacent pas la vérification des sources.</p></details>'
            '<details><summary>Valeurs réellement transmises au dernier appel</summary><ul>%s</ul></details></section>') % (
        e(ollama.get('url', '')), e(routing.get('fast_model') or ollama.get('model', '')), e(routing.get('complex_model') or ollama.get('model', '')),
        e(prof['label']), (' · version %d' % prof['version']) if prof.get('version') else '', adopt,
        ('<p class="m34-muted">Dernier test : %s.</p>' % e(test.get('summary', ''))) if isinstance(test, dict) else '',
        e(profil5634.INTRO), field('temperature', '0.05'), field('top_p', '0.05'), field('top_k', '1'), field('min_p', '0.01'), field('repeat_penalty', '0.05'),
        field('repeat_last_n', '1'), ' selected' if prof['context_mode'] == 'auto' else '', ' selected' if prof['context_mode'] == 'fixe' else '',
        e(str(ollama.get('num_ctx', ''))), e(profil5634.HELP['context']), last_rows or '<li class="m34-muted">Aucun appel enregistré.</li>')


def save_profile(desk, env, data, profile=None):
    from . import config567, profil5634
    path = config567._path(env)   # pylint: disable=protected-access
    current = json.loads(path.read_text(encoding='utf-8'))
    values = {}
    if profile == 'juridique':
        values = {'llm.profile': 'juridique', 'llm.context_mode': 'auto' if int((current.get('ollama') or {}).get('num_ctx') or 32768) == 32768 else 'fixe'}
    else:
        values['llm.profile'] = 'personnalise'
        for key in profil5634.SAMPLING:
            if key in data:
                values['llm.' + key] = profil5634.number(data[key], key)
        values['llm.context_mode'] = 'fixe' if data.get('context_mode') == 'fixe' else 'auto'
    values['llm.profile_version'] = profil5634.VERSION
    out = config567.save(desk, {'values': values, 'revision': int(current.get('config_revision567', 0))}, env)
    desk.audit('profil5634_enregistre', {'profile': values['llm.profile']})
    return {**out, 'message': 'Profil Juridique appliqué aux prochains travaux.' if profile == 'juridique' else 'Profil personnalisé enregistré ; il s’applique aux prochains travaux.'}


def test_profile(desk):
    from . import profil5634
    res = profil5634.neutral_test(desk.c)
    parts = ['configuration enregistrée' if res['configuration'] else 'configuration incomplète',
             'connexion testée' if res['connexion'] else 'connexion impossible',
             'modèle présent' if res['modele_present'] else 'modèle absent',
             'sortie JSON conforme' if res['json'] else 'sortie JSON non conforme', 'génération courte réussie' if res['generation'] else 'génération non obtenue']
    summary = ' · '.join(parts) + ((' · fenêtre maximale annoncée : %d' % res['limite_contexte']) if res.get('limite_contexte') else '')
    from datetime import datetime, timezone
    desk.setting('llm5634:last_test', {'at': datetime.now(timezone.utc).isoformat(), 'summary': summary, 'options': res['options'], 'ok': res['generation'] and res['json']})
    if res['erreur']:
        from .erreurs5634 import explain
        summary += ' — ' + explain(res['erreur'])
    return {'result': res, 'message': summary + ('. Valeurs utilisées : ' + ', '.join('%s=%s' % kv for kv in res['options'].items()) if res['options'] else '.'),
            'incompatibilites': res['incompatibilites']}


# ------------------------------------------------------------------------------------------------ API
def _post(env, auth, limit=260_000):
    if env.get('HTTP_ORIGIN') != auth['origin'] or not hmac.compare_digest(env.get('HTTP_X_CSRF_TOKEN', ''), auth['csrf']):
        raise Stop('origine_ou_csrf_refuse')
    length = int(env.get('CONTENT_LENGTH', '0') or 0)
    if length > limit:
        raise Stop('requete_trop_longue')
    if env.get('CONTENT_TYPE', '').split(';')[0] != 'application/json':
        raise Stop('json_requis')
    try:
        data = json.loads(env['wsgi.input'].read(length).decode('utf-8') or '{}')
    except (ValueError, UnicodeError):
        raise Stop('json_invalide') from None
    if not isinstance(data, dict):
        raise Stop('objet_json_requis')
    return data


def api(env, desk, auth, prefix, name, args, method):
    from .web440 import json_out
    from .web567 import actor
    owner, role = actor(env)
    route = name[len('m5634/'):].split('?', 1)[0]
    if method == 'GET':
        if route == 'dossiers':
            return json_out(search_matters(desk, args.get('q', ''), args.get('page', 1)))
        if route == 'documents':
            return json_out({'documents': documents(desk, str(args.get('matter') or ''), args.get('q', ''))})
        if route == 'releve':
            from .synthese5632 import full_index
            return json_out(full_index(desk, str(args.get('matter') or ''), str(args.get('path') or '')))
        if route.startswith('agenda/'):
            from . import agenda5634
            return json_out(agenda5634.api(desk, route, args, 'GET', owner))
        raise Stop('route_inconnue')
    if method != 'POST':
        raise Stop('methode_refusee')
    data = _post(env, auth, 9_000_000 if route == 'agenda/importer' else 260_000)
    if route.startswith('agenda/'):
        from . import agenda5634
        return json_out(agenda5634.api(desk, route, data, 'POST', owner))
    if role not in ('administrateur', 'avocat') and route not in ('alerte',):
        raise Stop('role_insuffisant')
    if route == 'decision/annuler':
        from . import decisions5634
        return json_out(decisions5634.cancel(desk, str(data.get('type') or ''), str(data.get('id') or ''), owner, role == 'administrateur', data.get('motive', '')))
    if route == 'alerte':
        from .proactive import act5634
        out = act5634(desk, str(data.get('signal') or ''), str(data.get('action') or ''), owner, int(data.get('hours') or 24))
        msg = {'read': 'Alerte marquée comme lue ; le point reste à traiter.', 'remind': 'Rappel programmé ; aucune échéance n’a été modifiée.',
               'done': 'Alerte marquée comme traitée.', 'reopen': 'Alerte rouverte.', 'undo': 'Dernière action annulée.'}.get(str(data.get('action')), 'Enregistré.')
        return json_out({**out, 'message': msg + ((' ' + out['warning']) if out.get('warning') else '')})
    if route == 'audience/preparer':
        return json_out(prepare_hearing(desk, data))
    if route == 'reviser':
        return json_out(submit_revision(desk, data))
    if route == 'recherche':
        question = str(data.get('question') or '').strip()
        if not 3 <= len(question) <= 3000:
            raise Stop('question_juridique_invalide')
        providers = [p for p in (data.get('providers') or []) if isinstance(p, str)][:10]
        job = desk.enqueue('recherche5634', {'question': question, 'matter': str(data.get('matter') or ''), 'providers': providers,
                                              'domaine': str(data.get('domaine') or '')[:120], 'territoire': str(data.get('territoire') or '')[:10],
                                              'date': str(data.get('date') or '')[:10]})
        return json_out({'job_id': job, 'message': 'Recherche lancée ; le résultat s’affiche ci-dessous dès qu’il est prêt (rechargez la page).'})
    if route in ('modeles/profil', 'modeles/restaurer', 'modeles/adopter'):
        if role != 'administrateur':
            raise Stop('role_insuffisant')
        return json_out(save_profile(desk, env, data, None if route == 'modeles/profil' else 'juridique'))
    if route == 'modeles/tester':
        return json_out(test_profile(desk))
    if route == 'pieces/ecritures':   # commandes déplacées de la recherche juridique vers le dossier
        return json_out({'job_id': desk.enqueue('identify_latest_writings', {'matter': str(data.get('matter') or ''), 'document_type': 'conclusions'}),
                         'message': 'Identification des dernières écritures lancée.'})
    if route == 'pieces/registre':
        return json_out({'job_id': desk.enqueue('refresh_exhibit_registry', {'matter': str(data.get('matter') or '')}), 'message': 'Actualisation du registre des pièces lancée.'})
    raise Stop('route_inconnue')
