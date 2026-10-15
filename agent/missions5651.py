"""5.6.51 (audit 5.6.50, A01 et A07) : « Prendre en charge une mission » et préparation proactive des prochaines étapes.

A01 — réutilise le moteur de missions 5.6.14 (plan typé, étapes reprenables avec réservation et reprise, livrable Word déposé et relu,
décisions, pause / reprise / arrêt) ; aucun second moteur. Ce module ajoute :
- le panneau (dossier préselectionné, consigne, fichiers choisis, résultat attendu) sans choix de modèle, connecteur ou compétence ;
- l'idempotence de la commande : un second clic rejoint la mission active ; une nouvelle tentative après un arrêt est une nouvelle
  mission historisée (« tentative n ») ;
- la carte de mission commune (dossier, objectif, étape courante, livrables, décisions attendues, état : terminé, résultat partiel,
  attente de décision, échec).

A07 — préparation d'audience ou d'échéance **confirmée** aux paliers réglés (J-14, J-7, J-2 par défaut ; réglages de préparation, pas
des délais légaux), par un planificateur durable : clé d'idempotence dossier + événement + version + palier ; fuseau du cabinet ;
report d'audience = nouvelles dates et préparations de l'ancienne date annulées (résultats déjà produits conservés) ; rattrapage
d'un seul palier après une indisponibilité ; rien pendant une pause du dossier, ni pour un dossier archivé ; une échéance à confirmer
n'est jamais préparée comme certaine. Mode par dossier : « À la demande » (défaut), « Préparation automatique ».
"""
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import re
from zoneinfo import ZoneInfo

from .common import Stop, clean_path, fold, load_matters, matter_display, under

SCHEMA = '''
CREATE TABLE IF NOT EXISTS d51_mission_requests(request_key TEXT PRIMARY KEY, matter TEXT NOT NULL, owner TEXT NOT NULL, base TEXT NOT NULL,
  attempt INTEGER NOT NULL, mission TEXT NOT NULL, created TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS d51_mission_requests_base ON d51_mission_requests(base, attempt);
CREATE TABLE IF NOT EXISTS d51_preparations(key TEXT PRIMARY KEY, matter TEXT NOT NULL, event_kind TEXT NOT NULL, event_id TEXT NOT NULL,
  event_version TEXT NOT NULL, event_date TEXT NOT NULL, palier INTEGER NOT NULL, due TEXT NOT NULL, state TEXT NOT NULL,
  mission TEXT NOT NULL DEFAULT '', reason TEXT NOT NULL DEFAULT '', created TEXT NOT NULL, updated TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS d51_preparations_matter ON d51_preparations(matter, state, due);
'''
GOALS = {'repondre': ('Répondre à des écritures adverses', 'conclusions'), 'actualiser': ('Actualiser nos conclusions', 'conclusions'),
         'audience': ('Préparer une audience', 'audience'), 'autre': ('Autre résultat (note, courrier, synthèse…)', 'livrable')}
MODES = {'a_la_demande': 'À la demande', 'preparation_automatique': 'Préparation automatique'}
PALIERS = (14, 7, 2)
PREP_STATES = {'prevue': 'Prévue', 'lancee': 'Lancée', 'annulee': 'Annulée (date modifiée ou événement annulé)', 'sautee': 'Rattrapée par un palier plus récent',
               'bloquee': 'Non lancée (dossier en pause ou archivé)'}
ACTIVE_STATES = ('active', 'decision', 'suspendue', 'suspendue_budget', 'bloquee', 'a_valider', 'suggested')


def _now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def _sha(*parts):
    return hashlib.sha256('|'.join(str(p) for p in parts).encode('utf-8')).hexdigest()


def ensure_schema(desk):
    desk.db.executescript(SCHEMA)


def _matter(desk, mid):
    m = next((x for x in load_matters(desk.c) if str(x['id']) == str(mid)), None)
    if not m:
        raise Stop('dossier_absent')
    return m


# ------------------------------------------------------------------------------------------------ A01
def start(desk, owner, data):
    """Mission depuis le panneau ; renvoie la mission (et « rejointe » si une mission identique est déjà active)."""
    from . import taches5614
    ensure_schema(desk)
    taches5614.ensure_schema(desk)
    m = _matter(desk, data.get('matter'))
    goal = str(data.get('objectif') or 'autre')
    if goal not in GOALS:
        raise Stop('objectif_mission_invalide')
    consigne = re.sub(r'\s+', ' ', str(data.get('consigne') or '')).strip()
    result = re.sub(r'\s+', ' ', str(data.get('resultat') or '')).strip()[:500]
    if len(consigne) < 3 and goal == 'autre':
        raise Stop('question_requise_12000_caracteres_maximum')
    files = data.get('fichiers') or []
    if isinstance(files, str):
        files = [x for x in files.splitlines() if x.strip()]
    roots = [m['path']] + list(m.get('paths') or [])
    chosen = []
    for f in files[:40]:
        path = clean_path(str(f))
        if not any(under(path, r) for r in roots):
            raise Stop('fichier_hors_dossier')
        chosen.append(path)
    label, parcours = GOALS[goal]
    objective = label + (' — ' + consigne if consigne else '') + ((' — résultat attendu : ' + result) if result else '')
    base = _sha(owner, m['id'], parcours, fold(objective), json.dumps(sorted(chosen)))[:40]
    for r in desk.db.execute('SELECT r.mission,m.state FROM d51_mission_requests r JOIN missions5614 m ON m.id=r.mission WHERE r.base=? ORDER BY r.attempt DESC',
                             (base,)).fetchall():
        if r['state'] in ACTIVE_STATES:
            return {**taches5614.get(desk, r['mission'], owner), 'rejointe': True}
        break
    attempt = 1 + desk.db.execute('SELECT COUNT(*) FROM d51_mission_requests WHERE base=?', (base,)).fetchone()[0]
    click = str(data.get('click_key') or '')
    key = 'm51-' + (_sha(base, click)[:40] if re.fullmatch(r'[A-Za-z0-9-]{16,80}', click) else _sha(base, attempt)[:40])
    known = desk.db.execute('SELECT mission FROM d51_mission_requests WHERE request_key=?', (key,)).fetchone()
    if known:   # double clic : même commande
        return {**taches5614.get(desk, known['mission'], owner), 'rejointe': True}
    mission = taches5614.create(desk, {'matter': m['id'], 'instruction': objective[:12000], 'parcours': parcours, 'request_key': key,
                                       'answers': {'fichiers_selectionnes': chosen, 'tentative': attempt} if chosen else {'tentative': attempt},
                                       'autonomy': 'prepare'}, owner)
    desk.db.execute('INSERT OR IGNORE INTO d51_mission_requests(request_key,matter,owner,base,attempt,mission,created) VALUES(?,?,?,?,?,?,?)',
                    (key, m['id'], owner, base, attempt, mission['id'], _now()))
    desk.db.commit()
    return {**mission, 'tentative': attempt}


def missions_of(desk, mid, owner, admin=False, prefix=''):
    from . import taches5614
    taches5614.ensure_schema(desk)
    sql = 'SELECT id FROM missions5614 WHERE matter=?' + ('' if admin else ' AND owner=?') + ' ORDER BY updated DESC LIMIT 20'
    out = []
    for r in desk.db.execute(sql, (mid,) if admin else (mid, owner)).fetchall():
        try:
            out.append(taches5614.get(desk, r['id'], owner, admin, prefix))
        except Stop:
            continue
    return out


def outcome_label(m):
    """États distincts : terminé, résultat partiel, attente de décision, échec, en cours."""
    tasks = m.get('tasks') or []
    if m.get('state') == 'annulee':
        return 'Arrêtée'
    if m.get('state') in ('validee', 'terminee'):
        return 'Terminée'
    if m.get('state') in ('decision', 'suspendue_budget') or m.get('decisions'):
        return 'En attente de votre décision'
    if any(t.get('run_state') == 'en_erreur' for t in tasks):
        return 'Échec d’une étape (reprise possible)'
    if m.get('state') == 'a_valider' or m.get('validation') == 'a_decider':
        return 'Projet prêt à relire'
    if m.get('state') == 'bloquee':
        return 'Résultat partiel avec réserves'
    if m.get('state') == 'suspendue':
        return 'En pause'
    return 'En cours'


def card_html(m, prefix=''):
    from html import escape as e
    tasks = m.get('tasks') or []
    current = next((t for t in tasks if t.get('run_state') in ('en_cours', 'en_attente', 'a_preparer')), None)
    path = ''
    for t in tasks:   # livrable : fichier Word réellement déposé et relu par l'étape d'assemblage
        content = (t.get('result') or {}).get('content')
        if t.get('task_type') == 'assemblage' and isinstance(content, dict) and content.get('path'):
            path = content['path']
    decisions = [d for d in (m.get('decisions') or []) if isinstance(d, dict)]
    done = len([t for t in tasks if t.get('run_state') == 'terminee'])
    control = lambda action, label, ghost=True: ('<form class="m5-form q51-inline" data-api="m568/mission5614/control" data-reload><input type="hidden" name="id" value="%s">'
                                                 '<input type="hidden" name="matter" value="%s"><input type="hidden" name="action" value="%s">'
                                                 '<button class="ax-btn%s" type="submit">%s</button></form>' % (e(m['id'], quote=True), e(m.get('matter', ''), quote=True),
                                                                                                                action, ' ghost' if ghost else '', label))
    buttons = ''
    if m.get('state') in ('active', 'decision', 'bloquee'):
        buttons = control('pause', 'Pause') + control('cancel', 'Arrêter')
    elif m.get('state') in ('suspendue',):
        buttons = control('resume', 'Reprendre', False) + control('cancel', 'Arrêter')
    return ('<article class="mi51-card"><header><strong>%s</strong><span class="mi51-state">%s</span></header><p class="mi51-goal">%s</p>'
            '<p class="ax-muted">Étape : %s · %d/%d étape(s) terminée(s)</p>%s%s<div class="mi51-actions">%s<a class="ax-btn ghost" href="%s">Détail</a></div></article>') % (
        e(m.get('title', '')), e(outcome_label(m)), e(str(m.get('objective', ''))[:300]), e(current['title'] if current else '—'), done, len(tasks),
        ('<p>Livrable : <code>%s</code></p>' % e(path.rsplit('/', 1)[-1])) if path else '',
        ('<p class="mi51-wait">%d décision(s) attendue(s) : %s</p>' % (len(decisions), e('; '.join(str(d.get('question', ''))[:120] for d in decisions[:2])))) if decisions else '',
        buttons, e(m.get('url') or (prefix + '/missions-complexes?id=' + m['id']), quote=True))


def panel_html(mid, files=(), prefix=''):
    """Panneau « Prendre en charge une mission » : dossier préselectionné, consigne, fichiers, résultat attendu ; aucune option technique."""
    from html import escape as e
    goals = ''.join('<label class="mi51-goal-opt"><input type="radio" name="objectif" value="%s"%s> %s</label>' % (k, ' checked' if k == 'repondre' else '', e(v[0]))
                    for k, v in GOALS.items())
    opts = ''.join('<option value="%s">%s</option>' % (e(f['path'], quote=True), e(f['name'])) for f in files[:300])
    return ('<details class="mi51-panel" id="mi51-panel"><summary class="ax-btn">Prendre en charge une mission</summary>'
            '<form class="m5-form" data-api="m5651/mission" data-reload><input type="hidden" name="matter" value="%s">'
            '<input type="hidden" name="click_key" data-mi51-key>'
            '<fieldset><legend>Résultat attendu</legend>%s</fieldset>'
            '<label>Consigne<textarea name="consigne" rows="3" maxlength="4000" placeholder="Ex. Répondre aux conclusions adverses du 3 octobre en maintenant nos demandes."></textarea></label>'
            '<input type="hidden" name="fichiers" value=""><label>Fichiers à utiliser en priorité (facultatif)<select data-mi51-files multiple size="5">%s</select></label>'
            '<label>Précision sur le livrable (facultatif)<input name="resultat" maxlength="500" placeholder="Ex. projet Word de conclusions récapitulatives"></label>'
            '<p class="ax-muted">L’agent prépare un plan, lit les sources, rédige et contrôle ; il demande les décisions indispensables. Rien n’est envoyé ni déposé.</p>'
            '<button class="ax-btn" type="submit">Lancer la mission</button><p role="status" class="m5-status"></p></form></details>') % (e(mid, quote=True), goals, opts)


# ------------------------------------------------------------------------------------------------ A07
def mode(desk, mid):
    value = (desk.settings('preparation5651:' + str(mid), {}) or {})
    return {'mode': value.get('mode') if value.get('mode') in MODES else 'a_la_demande',
            'paliers': [int(x) for x in value.get('paliers') or PALIERS if str(x).isdigit() and 0 < int(x) <= 60][:5] or list(PALIERS)}


def set_mode(desk, mid, author, data):
    _matter(desk, mid)
    value = str(data.get('mode') or '')
    if value not in MODES:
        raise Stop('mode_preparation_invalide')
    paliers = data.get('paliers') or list(PALIERS)
    if isinstance(paliers, str):
        paliers = [x for x in re.split(r'[,; ]+', paliers) if x]
    try:
        paliers = sorted({int(x) for x in paliers if 0 < int(x) <= 60}, reverse=True)[:5]
    except (TypeError, ValueError):
        raise Stop('paliers_invalides') from None
    desk.setting('preparation5651:' + str(mid), {'mode': value, 'paliers': paliers or list(PALIERS), 'by': author, 'at': _now()})
    if value == 'a_la_demande':   # préparations futures annulées ; les résultats déjà produits restent
        desk.db.execute("UPDATE d51_preparations SET state='annulee',reason='préparation automatique désactivée',updated=? WHERE matter=? AND state='prevue'",
                        (_now(), mid))
        desk.db.commit()
    return mode(desk, mid)


def _tz(desk):
    try:
        return ZoneInfo(desk.c.get('calendar', {}).get('timezone') or 'Europe/Paris')
    except Exception:   # noqa: BLE001
        return ZoneInfo('Europe/Paris')


def confirmed_events(desk, mid):
    """Événements confirmés seulement : échéances confirmées ou corrigées, audiences enregistrées par l'avocat."""
    out = []
    try:
        for r in desk.db.execute("SELECT id,rule_id,due,status FROM deadlines450 WHERE matter=? AND status IN ('confirmee','manuel') AND due<>''", (mid,)):
            out.append({'kind': 'echeance', 'id': r['id'], 'date': r['due'][:10], 'label': 'Échéance ' + r['rule_id'], 'version': _sha(r['due'], r['status'])[:16]})
    except Exception:   # noqa: BLE001
        pass
    try:
        for r in desk.db.execute("SELECT id,title,dates FROM d51_events WHERE matter=? AND nature='audience' AND review='enregistre'", (mid,)):
            d = json.loads(r['dates'] or '{}').get('evenement') or json.loads(r['dates'] or '{}').get('audience') or ''
            if re.fullmatch(r'\d{4}-\d{2}-\d{2}', d[:10] or ''):
                out.append({'kind': 'audience', 'id': r['id'], 'date': d[:10], 'label': r['title'][:120], 'version': _sha(d)[:16]})
    except Exception:   # noqa: BLE001
        pass
    return out


def plan(desk, mid, today=None):
    """Préparations prévues pour les événements confirmés (version courante) ; les préparations d'une ancienne version sont annulées."""
    ensure_schema(desk)
    today = today or datetime.now(_tz(desk)).date()
    cfg = mode(desk, mid)
    now = _now()
    current = set()
    for ev in confirmed_events(desk, mid):
        event_day = date.fromisoformat(ev['date'])
        if event_day < today:
            continue
        for n in cfg['paliers']:
            key = _sha(mid, ev['kind'], ev['id'], ev['version'], n)[:40]
            current.add(key)
            desk.db.execute('INSERT OR IGNORE INTO d51_preparations(key,matter,event_kind,event_id,event_version,event_date,palier,due,state,created,updated) '
                            'VALUES(?,?,?,?,?,?,?,?,?,?,?)', (key, mid, ev['kind'], ev['id'], ev['version'], ev['date'], n,
                                                              (event_day - timedelta(days=n)).isoformat(), 'prevue', now, now))
    for r in desk.db.execute("SELECT key FROM d51_preparations WHERE matter=? AND state='prevue'", (mid,)).fetchall():
        if r['key'] not in current:   # date reportée, événement annulé ou non confirmé : aucune ancienne relance
            desk.db.execute("UPDATE d51_preparations SET state='annulee',reason='événement modifié, annulé ou non confirmé',updated=? WHERE key=?", (now, r['key']))
    desk.db.commit()
    return [dict(r) for r in desk.db.execute('SELECT * FROM d51_preparations WHERE matter=? ORDER BY event_date, palier DESC', (mid,))]


def _closed(desk, mid):
    try:
        row = desk.db.execute('SELECT state FROM matter_portfolio WHERE matter=?', (mid,)).fetchone()
        return bool(row and row['state'] == 'archived')
    except Exception:   # noqa: BLE001
        return False


def run_due(desk, mid, today=None, owner='cabinet'):
    """Lance au plus UN palier échu par événement (rattrapage unique) ; les paliers plus anciens sont marqués rattrapés."""
    from .pause5629 import is_paused
    preps = plan(desk, mid, today)
    today = today or datetime.now(_tz(desk)).date()
    if mode(desk, mid)['mode'] != 'preparation_automatique':
        return []
    launched = []
    by_event = {}
    for p in preps:
        if p['state'] == 'prevue' and date.fromisoformat(p['due']) <= today:
            by_event.setdefault((p['event_kind'], p['event_id'], p['event_version']), []).append(p)
    now = _now()
    for evkey, due in by_event.items():
        due.sort(key=lambda p: p['palier'])                # le palier le plus proche de l'événement
        latest, older = due[0], due[1:]
        for p in older:
            desk.db.execute("UPDATE d51_preparations SET state='sautee',reason=?,updated=? WHERE key=?", ('rattrapé par le palier J-%d' % latest['palier'], now, p['key']))
        if is_paused(desk, mid) or _closed(desk, mid):
            desk.db.execute("UPDATE d51_preparations SET state='bloquee',reason=?,updated=? WHERE key=?",
                            ('dossier en pause' if is_paused(desk, mid) else 'dossier archivé', now, latest['key']))
            continue
        still = {(e['kind'], e['id'], e['version']) for e in confirmed_events(desk, mid)}
        if evkey not in still:                              # revérifié juste avant l'effet
            desk.db.execute("UPDATE d51_preparations SET state='annulee',reason='événement modifié avant lancement',updated=? WHERE key=?", (now, latest['key']))
            continue
        from . import taches5614
        label = 'audience' if latest['event_kind'] == 'audience' else 'échéance'
        mission = taches5614.create(desk, {'matter': mid, 'parcours': 'audience', 'autonomy': 'prepare', 'request_key': 'prep51-' + latest['key'][:40],
                                           'instruction': 'Préparation automatique J-%d de l’%s du %s : dossier d’audience (synthèse actualisée, chronologie utile, '
                                                          'prétentions en présence, pièces clés et pièces manquantes, questions, projet de note de plaidoirie).'
                                                          % (latest['palier'], label, latest['event_date'])}, owner)
        desk.db.execute("UPDATE d51_preparations SET state='lancee',mission=?,updated=? WHERE key=?", (mission['id'], now, latest['key']))
        launched.append(latest['key'])
    desk.db.commit()
    return launched


def schedule(desk, stamp=None):
    """Appelé par le cycle de surveillance : au plus une vérification par dossier et par heure."""
    ensure_schema(desk)
    stamp = stamp or datetime.now(timezone.utc).timestamp()
    if stamp - float(desk.settings('preparation5651:last', 0) or 0) < 3600:
        return []
    desk.setting('preparation5651:last', stamp)
    jobs = []
    for m in load_matters(desk.c, unfiltered=True):
        if mode(desk, m['id'])['mode'] == 'preparation_automatique':
            try:
                jobs += run_due(desk, m['id'])
            except Stop:
                continue
    return jobs


def preparations_html(desk, mid, prefix=''):
    from html import escape as e
    cfg = mode(desk, mid)
    preps = [p for p in plan(desk, mid) if p['state'] in ('prevue', 'lancee', 'bloquee')]
    rows = ''.join('<li>%s · J-%d (le %s) — %s%s</li>' % (e(p['event_date']), p['palier'], e(p['due']), e(PREP_STATES[p['state']]),
                                                          (' · <a href="%s">mission</a>' % e(prefix + '/missions-complexes?id=' + p['mission'], quote=True)) if p['mission'] else '')
                   for p in preps[:20])
    opts = ''.join('<option value="%s"%s>%s</option>' % (k, ' selected' if k == cfg['mode'] else '', e(v)) for k, v in MODES.items())
    return ('<section class="d51-card" id="d51-preparation"><h3>Préparation prévue</h3>'
            '<p class="ax-muted">Paliers de préparation (pas des délais légaux) avant une audience enregistrée ou une échéance confirmée. Les résultats arrivent '
            'dans « À relire » ; rien n’est envoyé.</p><ul class="d51-list">%s</ul>'
            '<form class="m5-form d51-inline" data-api="m5651/preparation" data-reload><input type="hidden" name="matter" value="%s">'
            '<label>Mode<select name="mode">%s</select></label><label>Paliers (jours)<input name="paliers" value="%s" size="8"></label>'
            '<button class="ax-btn ghost" type="submit">Enregistrer</button><p role="status" class="m5-status"></p></form></section>') % (
        rows or '<li class="ax-muted">Aucune audience enregistrée ni échéance confirmée à venir.</li>', e(mid, quote=True), opts, e(', '.join(str(x) for x in cfg['paliers'])))
