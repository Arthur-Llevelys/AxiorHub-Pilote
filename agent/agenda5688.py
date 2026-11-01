"""5.6.88 : pages et routes du lot agenda : suivi des audiences, Mes tâches, arbitrage des agendas distants, passage de surveillance.

Les routes sont ``/api440/m5634/agenda/audience/*``, ``.../tache/*``, ``.../rappel/*`` et ``.../distant/*`` : elles relèvent du droit « agenda »
déjà contrôlé par ``membres5650`` et du rôle assistant(e) déjà admis pour ``m5634/agenda/``. Chaque objet désigné par son identifiant repasse
par le contrôle des dossiers réservés côté service.
"""
from datetime import date, datetime, timedelta
from html import escape as e
from urllib.parse import urlencode

from .common import Stop, load_matters, matter_display

LAWYERS = ('administrateur', 'avocat')


def tick(desk, stamp=None):
    """Surveillance : fiches manquantes, audiences lues dans Nextcloud, rappels dus. Le réseau distant passe par le travail ``propagation5688``."""
    from . import audiences5688, rappels5688, taches5688
    audiences5688.migrate(desk)
    audiences5688.sync_nextcloud(desk)
    taches5688.migrate(desk)
    return rappels5688.tick(desk, stamp)


# ------------------------------------------------------------------------------------------------ routes
def api(desk, route, data, method, owner, role, auth=None):
    from . import audiences5688 as au
    from . import propagation5688 as pr
    from . import rappels5688 as rp
    from . import taches5688 as ta
    sub = route[len('agenda/'):]
    if method == 'GET':
        if sub == 'audience/liste':
            rows = au.listing(desk, str(data.get('avocat') or ''), str(data.get('de') or ''), str(data.get('a') or ''), str(data.get('statut') or ''), str(data.get('matter') or ''))
            return {'audiences': [au.public(r) for r in rows]}
        if sub == 'audience/fiche':
            return {'audience': au.public(au.get(desk, data.get('id')))}
        if sub == 'tache/liste':
            return {'taches': [{k: t.get(k) for k in ('id', 'title', 'matter', 'due', 'statut', 'responsable')} for t in ta.mine(desk, owner, str(data.get('vue') or 'moi'))]}
        raise Stop('route_inconnue')
    if sub == 'audience/enregistrer':
        return au.save(desk, data, owner, auth)
    if sub == 'audience/copier':
        return au.propose_copy(desk, data.get('id'), owner)
    if sub == 'distant/arbitrer':
        return pr.resolve(desk, data.get('id'), data.get('provider'), data.get('choix'), owner)
    if sub == 'distant/detail':
        if role not in LAWYERS:
            raise Stop('role_insuffisant')
        return pr.set_details(desk, data.get('detail') in (True, 'true', 'on', '1'), owner)
    if sub == 'tache/creer':
        return ta.create(desk, data, owner, auth)
    if sub == 'tache/courriel':
        return ta.from_mail(desk, data, owner, auth)
    if sub == 'tache/modifier':
        return ta.update(desk, data, owner, auth)
    if sub == 'rappel/preferences':
        return rp.save_preferences(desk, owner, data, auth)
    if sub == 'rappel/lu':
        return rp.mark_read(desk, owner, data.get('id') or '')
    if sub == 'rappel/cabinet':
        if role not in LAWYERS:
            raise Stop('role_insuffisant')
        return rp.save_cabinet(desk, data, owner)
    raise Stop('route_inconnue')


# ------------------------------------------------------------------------------------------------ page des audiences
def _options(values, chosen):
    return ''.join('<option value="%s"%s>%s</option>' % (e(k, quote=True), ' selected' if k == chosen else '', e(v)) for k, v in values)


def _timing_fields(desk, h):
    """Horaire prérempli depuis l'événement : une correction de la fiche ne déplace ni ne raccourcit l'audience par mégarde."""
    from .agenda5634 import _tz
    place = '<label>Lieu<input name="location" maxlength="200" value="%s"></label>' % e(h.get('location', ''), quote=True)
    if h.get('lecture_seule'):
        return ''
    if h.get('all_day'):
        return '<div class="t88-row">%s</div>' % place
    value, minutes = '', 60
    if h:
        begins, ends = datetime.fromisoformat(h['starts']), datetime.fromisoformat(h['ends'])
        value, minutes = begins.astimezone(_tz(desk)).strftime('%Y-%m-%dT%H:%M'), int((ends - begins).total_seconds() // 60)
    return ('<div class="t88-row"><label>Date et heure<input type="datetime-local" name="start" value="%s"%s></label><label>Durée (minutes)<input type="number" name="duration_minutes" '
            'min="5" max="1440" step="5" value="%d"></label>%s</div>') % (value, '' if h else ' required', minutes, place)


def _hearing_fields(desk, known, labels, h=None):
    from . import audiences5688 as au
    from .taches5688 import member_options
    h = h or {}
    matter = h.get('matter', '')
    timing = _timing_fields(desk, h)
    return ('%s<div class="t88-row"><label>Dossier<select name="matter"><option value="">Sans dossier</option>%s</select></label><label>Avocat qui s’y rend<select name="avocat">%s</select></label>'
            '<label>Type<select name="type">%s</select></label></div><div class="t88-row"><label>Juridiction<input name="juridiction" maxlength="200" value="%s"></label>'
            '<label>Chambre<input name="chambre" maxlength="120" value="%s"></label><label>RG<input name="rg" maxlength="40" value="%s"></label></div>'
            '<label>Notes (restent dans AxiorHub)<textarea name="notes" rows="2" maxlength="4000">%s</textarea></label>') % (
        timing, _options(labels, matter), member_options(known, h.get('avocat', ''), au.SANS_AVOCAT), _options(au.TYPES.items(), h.get('type_audience', 'autre')),
        e(h.get('juridiction', ''), quote=True), e(h.get('chambre', ''), quote=True), e(h.get('rg', ''), quote=True), e(h.get('notes', '')))


def suivi_html(desk, auth, prefix, args, role):
    from . import audiences5688 as au
    from .membres5650 import viewer_hidden
    if args.get('id'):
        return detail_html(desk, auth, prefix, str(args['id']), role)
    known = au.members(desk, auth)
    hidden = viewer_hidden() or frozenset()
    labels = sorted(((m['id'], matter_display(m)) for m in load_matters(desk.c) if m['id'] not in hidden), key=lambda x: x[1].casefold())
    avocat, debut, fin, statut = (str(args.get(k) or '') for k in ('avocat', 'de', 'a', 'statut'))
    today = date.today()
    debut, fin = debut or today.isoformat(), fin or (today + timedelta(days=90)).isoformat()
    try:
        rows = au.listing(desk, avocat, debut, fin, statut, known=known)
        problem = ''
    except Stop as ex:
        from .erreurs5634 import explain
        rows, problem = [], '<p class="notice">%s</p>' % e(explain(str(ex)))
    people = [(au.SANS, au.SANS_AVOCAT)] + sorted(((m['owner'], str(m.get('name') or m['owner'])) for m in known.values()), key=lambda x: x[1].casefold())
    filters = ('<form method="get" action="%s" class="t88-filters"><input type="hidden" name="onglet" value="suivi"><label>Avocat<select name="avocat"><option value="">Tous</option>%s</select></label>'
               '<label>Du<input type="date" name="de" value="%s"></label><label>Au<input type="date" name="a" value="%s"></label><label>Statut<select name="statut"><option value="">Tous</option>%s</select></label>'
               '<button class="ax-btn ghost" type="submit">Filtrer</button></form>') % (
        e(prefix + '/audiences', quote=True), _options(people, avocat), e(debut, quote=True), e(fin, quote=True), _options(au.STATUTS.items(), statut))
    names = dict(labels)
    items = ''.join('<li class="t88-item"><a class="t88-title" href="%s">%s — %s</a><div class="t88-chips">%s<span class="t88-chip%s">%s</span><span class="t88-chip">%s</span></div></li>' % (
        e(prefix + '/audiences?onglet=suivi&id=' + h['id'], quote=True), e(h['quand']), e(h['title']),
        ''.join('<span class="t88-chip">%s</span>' % e(x) for x in (h['juridiction'], ('RG ' + h['rg']) if h['rg'] else '', names.get(h['matter'], '')) if x),
        ' t88-late' if h['sans_avocat'] else '', e(h['avocat_nom']), e(h['statut_label'])) for h in rows)
    add = ('<details class="t88-add"><summary>Ajouter une audience</summary><form class="m5-form t88-form" data-api="m5634/agenda/audience/enregistrer" data-reload="1">%s'
           '<button class="ax-btn" type="submit">Enregistrer l’audience</button><p role="status" class="m5-status"></p></form></details>') % _hearing_fields(desk, known, labels)
    return filters + problem + add + ('<ul class="t88-list">%s</ul>' % items if items else '<p class="t88-empty">Aucune audience sur cette période.</p>') + _agendas_html(desk, role)


def _agendas_html(desk, role):
    from . import propagation5688 as pr
    data = pr.overview(desk)

    def line(item):
        waiting = sum(v for k, v in item['etats'].items() if k in ('conflit', 'supprime_distant', 'erreur'))
        return '<li><strong>%s</strong> : %s%s%s</li>' % (
            e(item['label']), 'relié' if item['actif'] else 'non relié', (' (%s)' % e(item['compte'])) if item['compte'] else '', (' · %d à arbitrer' % waiting) if waiting else '')
    detail = ''
    if role in LAWYERS:
        detail = ('<form class="m5-form t88-form" data-api="m5634/agenda/distant/detail" data-reload="1"><label class="m50-check"><input type="checkbox" name="detail"%s> Transmettre le détail '
                  '(intitulé, juridiction, RG, lieu) ; sans cette case, seul un intitulé neutre part</label><button class="ax-btn ghost" type="submit">Enregistrer</button>'
                  '<p role="status" class="m5-status"></p></form>') % (' checked' if data['detail'] else '')
    return ('<section class="ax-card t88-agendas"><h2>Agendas Outlook et Google</h2><ul>%s%s</ul><p class="ax-muted">Nextcloud reste l’agenda de référence. Une audience saisie ici est copiée dans '
            'les agendas reliés (Paramètres › Connexions) ; une modification faite là-bas n’est jamais écrasée sans votre choix. Les notes ne sont jamais transmises.</p>%s</section>') % (
        line(data['outlook']), line(data['google']), detail)


def _distant_html(desk, hearing):
    from . import propagation5688 as pr
    rows = pr.status(desk, pr.ref_of(hearing))
    if not rows:
        button = ''
        if not hearing['propagate']:
            button = ('<form class="m5-form m5-inline" data-api="m5634/agenda/audience/copier" data-reload="1"><input type="hidden" name="id" value="%s">'
                      '<button class="ax-btn ghost" type="submit">Copier vers les agendas</button></form>') % e(hearing['id'], quote=True)
        return '<p class="ax-muted">Pas de copie dans Outlook ou Google.</p>' + button

    def choice(provider, value, label):
        return ('<form class="m5-form m5-inline" data-api="m5634/agenda/distant/arbitrer" data-reload="1"><input type="hidden" name="id" value="%s"><input type="hidden" name="provider" value="%s">'
                '<input type="hidden" name="choix" value="%s"><button class="ax-btn ghost" type="submit">%s</button></form>') % (e(hearing['id'], quote=True), e(provider, quote=True), value, e(label))
    out = ''
    for r in rows:
        actions = ''
        if r['state'] == 'conflit':
            actions = choice(r['provider'], 'garder_axiorhub', 'Garder la version d’AxiorHub')
            if not hearing['lecture_seule'] and r['distant'].get('start'):
                actions += choice(r['provider'], 'reprendre_distant', 'Reprendre la version distante')
            actions += choice(r['provider'], 'arreter', 'Arrêter le suivi')
        elif r['state'] == 'supprime_distant':
            actions = choice(r['provider'], 'recreer', 'Recréer l’événement') + choice(r['provider'], 'arreter', 'Arrêter le suivi')
        elif r['state'] == 'erreur':
            actions = choice(r['provider'], 'reprendre', 'Relancer la copie')
        seen = ''
        if r['distant'].get('start'):
            seen = ' <small>Version distante : %s, %s</small>' % (e(str(r['distant']['start'])[:16].replace('T', ' ')), e(str(r['distant'].get('title') or '')[:120]))
        out += '<li><strong>%s</strong> : %s%s%s</li>' % (e(r['label']), e(r['state_label']), seen, actions)
    return '<ul>%s</ul>' % out


def detail_html(desk, auth, prefix, ident, role):
    from . import audiences5688 as au
    from . import taches5688
    from .membres5650 import viewer_hidden
    h = au.get(desk, ident)
    known = au.members(desk, auth)
    hidden = viewer_hidden() or frozenset()
    labels = sorted(((m['id'], matter_display(m)) for m in load_matters(desk.c) if m['id'] not in hidden), key=lambda x: x[1].casefold())
    facts = (('Date', h['quand']), ('Avocat', h['avocat_nom']), ('Juridiction', h['juridiction'] or '—'), ('Chambre', h['chambre'] or '—'), ('RG', h['rg'] or '—'), ('Type', h['type_label']),
             ('Lieu', h['location'] or '—'), ('Statut', h['statut_label']))
    sheet = '<dl class="t88-facts">%s</dl>' % ''.join('<dt>%s</dt><dd%s>%s</dd>' % (e(k), ' class="t88-late"' if k == 'Avocat' and h['sans_avocat'] else '', e(v)) for k, v in facts)
    source = ''
    if h['lecture_seule']:
        source = '<p class="ax-muted">Date et lieu viennent de l’agenda Nextcloud : ils se modifient là-bas.</p>'
    edit = ('<details class="t88-edit"%s><summary>Modifier</summary><form class="m5-form t88-form" data-api="m5634/agenda/audience/enregistrer" data-reload="1">'
            '<input type="hidden" name="id" value="%s"><input type="hidden" name="version" value="%s">%s<label>Statut<select name="statut">%s</select></label>'
            '<button class="ax-btn" type="submit">Enregistrer</button><p role="status" class="m5-status"></p></form></details>') % (
        ' open' if h['sans_avocat'] else '', e(h['id'], quote=True), e(str(h['version']), quote=True), _hearing_fields(desk, known, labels, h), _options(au.STATUTS.items(), h['statut']))
    tasks = ''.join('<li>%s <small>%s</small></li>' % (e(t['title']), e(taches5688.STATUTS.get(t['statut'], ''))) for t in au.linked_tasks(desk, h['id']))
    fiche = ''
    if h['matter']:
        fiche = '<p><a class="ax-btn ghost" href="%s">Fiche de préparation du dossier</a></p>' % e(prefix + '/audiences?' + urlencode({'onglet': 'fiche', 'matter': h['matter']}), quote=True)
    return ('<p><a href="%s">← Toutes les audiences</a></p><section class="ax-card"><h2>%s</h2>%s%s%s%s%s</section>'
            '<section class="ax-card"><h2>Tâches liées</h2>%s%s</section><section class="ax-card"><h2>Agendas Outlook et Google</h2>%s</section>') % (
        e(prefix + '/audiences?onglet=suivi', quote=True), e(h['title']), sheet, ('<p class="t88-notes">%s</p>' % e(h['notes'])) if h['notes'] else '', source, edit, fiche,
        ('<ul>%s</ul>' % tasks) if tasks else '<p class="ax-muted">Aucune tâche liée.</p>',
        taches5688.add_form(prefix, known, origine='audience', ref=h['id'], matter=h['matter'], owner=h['avocat'], heading='Ajouter une tâche pour cette audience'),
        _distant_html(desk, h))


def mes_taches_page(desk, auth, prefix, env, args):
    from . import taches5688
    from .web567 import actor
    owner, role = actor(env)
    taches5688.schema(desk)
    from . import audiences5688
    audiences5688.schema(desk)
    return taches5688.page_html(desk, auth, prefix, args, owner, role)
