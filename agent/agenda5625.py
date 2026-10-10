"""5.6.25 : ajouter soi-même un événement depuis « Agenda et tâches », dans un ou plusieurs agendas configurés.

Les agendas proposés sont ceux du connecteur multi-agendas (5.6.8) : agendas Nextcloud autorisés dans Paramètres, agendas
CalDAV enregistrés (Paramètres › Agendas) et Google Agenda s'il est connecté et autorisé. Chaque dépôt passe par
``calendar568.deposit`` : verrou par agenda, identité stable, aucun invité, puis relecture de l'événement déposé. Pour Google, le
titre et la description ne sont transmis que si l'agenda l'autorise (« détails transmis ») ; sinon un titre neutre est déposé.
"""
from datetime import date, datetime, timedelta
import re
import secrets

from .common import Stop, load_matters


def targets(desk, owner):
    """Agendas où l'avocat peut ajouter un événement (sans aucun secret)."""
    from . import calendar568
    out = []
    try:
        rows = calendar568.effective_targets(desk, owner)
    except Stop:
        rows = []
    for t in rows:
        details = t['provider'] != 'google' or bool(t['config'].get('include_details'))
        out.append({'id': t['id'], 'label': t['label'], 'provider': t['provider'], 'details': details})
    return out


def _tz(desk, owner):
    from . import settings568
    try:
        return settings568.profile(desk, owner)['timezone']
    except Exception:
        return desk.c.get('calendar', {}).get('timezone', 'Europe/Paris')


def build_event(desk, owner, data):
    """Événement validé : titre, jour, heures (ou journée entière), description, dossier facultatif."""
    from zoneinfo import ZoneInfo
    title = re.sub(r'\s+', ' ', str(data.get('title') or '')).strip()
    if not 2 <= len(title) <= 250:
        raise Stop('intitule_evenement_invalide')
    try:
        day = date.fromisoformat(str(data.get('date') or ''))
    except ValueError:
        raise Stop('date_evenement_invalide') from None
    description = str(data.get('description') or '').strip()[:1900]
    location = re.sub(r'\s+', ' ', str(data.get('location') or '')).strip()[:200]
    if location:
        description = ('Lieu : ' + location + ('\n' + description if description else ''))[:1900]
    matter = str(data.get('matter') or '')
    if matter and matter not in {m['id'] for m in load_matters(desk.c)}:
        raise Stop('dossier_absent')
    all_day = data.get('all_day') in (True, 'true', 'on', '1', 'oui')
    if all_day:
        start, end = day.isoformat(), (day + timedelta(days=1)).isoformat()
    else:
        tz = ZoneInfo(_tz(desk, owner))
        try:
            h1 = datetime.strptime(str(data.get('start') or ''), '%H:%M').time()
            h2 = datetime.strptime(str(data.get('end') or ''), '%H:%M').time() if data.get('end') else None
        except ValueError:
            raise Stop('heure_evenement_invalide') from None
        begins = datetime.combine(day, h1, tz)
        ends = datetime.combine(day, h2, tz) if h2 else begins + timedelta(minutes=60)
        if ends <= begins:
            raise Stop('fin_avant_debut')
        start, end = begins.isoformat(), ends.isoformat()
    return {'key': 'manuel-' + secrets.token_hex(12), 'title': title, 'description': description, 'start': start, 'end': end,
            'all_day': all_day, 'kind': 'rendez-vous'}, matter, day


def create(desk, owner, data):
    """Dépose l'événement dans chaque agenda choisi et rend compte agenda par agenda (aucun échec masqué)."""
    from . import calendar568
    from .web440 import human
    event, matter, day = build_event(desk, owner, data)
    wanted = data.get('targets') or data.get('target') or []
    if isinstance(wanted, str):
        wanted = [x for x in wanted.split(',') if x]
    available = {t['id']: t for t in calendar568.effective_targets(desk, owner)}
    chosen = [available[t] for t in wanted if t in available]
    if not chosen:
        raise Stop('agenda_a_choisir' if available else 'aucun_agenda_configure')
    done, failed = [], []
    for target in chosen:
        try:
            calendar568.deposit(desk, owner, matter, event, target)
            done.append(target['label'])
        except Stop as ex:
            failed.append('%s : %s' % (target['label'], human(str(ex))))
    try:   # affichage immédiat dans la semaine (agendas Nextcloud lus par l'agenda)
        from .workplan import calendar_events
        calendar_events(desk, (day - timedelta(days=1)).isoformat(), (day + timedelta(days=2)).isoformat(), refresh=True)
    except Exception:
        pass
    desk.audit('agenda5625_evenement_ajoute', {'owner': owner, 'targets': len(chosen), 'deposes': len(done), 'echecs': len(failed), 'matter': matter})
    if not done:
        raise Stop('evenement_non_depose: ' + ' ; '.join(failed)[:300])
    message = 'Événement « %s » ajouté à : %s.' % (event['title'], ', '.join(done))
    if failed:
        message += ' Non ajouté : ' + ' ; '.join(failed) + '.'
    return {'message': message, 'deposes': done, 'echecs': failed}


def form_html(prefix, day, matters):
    """Formulaire « Ajouter un événement » (agendas chargés pour l'utilisateur connecté par v5625.js)."""
    from html import escape as e
    options = ''.join('<option value="%s">%s</option>' % (e(m['id'], quote=True), e(m.get('client_name') or m['id'])) for m in matters[:400])
    return ('<details class="ws-task-editor ag5625" id="ag5625"><summary>＋ Ajouter un événement</summary>'
            '<form class="ag5625-form" method="post" novalidate>'
            '<label>Intitulé<input name="title" required minlength="2" maxlength="250" placeholder="Rendez-vous client, audience, appel…"></label>'
            '<div class="ag5625-row"><label>Jour<input type="date" name="date" required value="%s"></label>'
            '<label>Début<input type="time" name="start" value="09:00"></label><label>Fin<input type="time" name="end" value="10:00"></label>'
            '<label class="m5-check"><input type="checkbox" name="all_day"> Journée entière</label></div>'
            '<label>Lieu (facultatif)<input name="location" maxlength="200"></label>'
            '<label>Dossier (facultatif)<select name="matter"><option value="">Sans dossier</option>%s</select></label>'
            '<label>Note (facultative)<textarea name="description" rows="2" maxlength="1900"></textarea></label>'
            '<fieldset class="ag5625-targets"><legend>Agendas</legend><p class="ax-muted" data-ag5625-targets>Lecture des agendas configurés…</p>'
            '<input type="hidden" name="targets" value=""></fieldset>'
            '<button class="ax-btn" type="submit">Ajouter à l’agenda</button></form></details>') % (e(day.isoformat(), quote=True), options)
