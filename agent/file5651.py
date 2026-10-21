"""5.6.51 (audit 5.6.50, A06) : file unique « À décider / À relire ».

Une décision est un objet unique, identifié par dossier + objet/version + décision attendue ; Aujourd'hui, Dossier et Produire en
affichent la même instance. Chaque élément porte une **révision** calculée sur l'objet : une décision prise sur une révision dépassée
échoue proprement (« version en conflit ») et l'élément est marqué périmé. La priorité ne vient jamais du modèle : échéance confirmée
proche, blocage d'un engagement, demande expresse de l'avocat. La validation regroupée n'accepte que des éléments homogènes et exclut
les choix stratégiques et les écritures externes. Rien n'est envoyé, déposé ni facturé depuis cette file.

Sources : rapprochements de parties à confirmer, documents à classer (un seul élément groupé), informations proposées (un seul élément
groupé : pas 130 cartes), contradictions de montants, lacunes qui bloquent un engagement, propositions de relecture Word, projets
terminés des missions et documents produits à relire.
"""
from datetime import date, datetime, timedelta, timezone
import hashlib
import json

from .common import Stop, load_matters, matter_display

SCHEMA = '''
CREATE TABLE IF NOT EXISTS d51_queue(key TEXT PRIMARY KEY, matter TEXT NOT NULL, item_class TEXT NOT NULL, state TEXT NOT NULL,
  revision TEXT NOT NULL, author TEXT NOT NULL DEFAULT '', motif TEXT NOT NULL DEFAULT '', until TEXT NOT NULL DEFAULT '', at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS d51_queue_log(id INTEGER PRIMARY KEY AUTOINCREMENT, key TEXT NOT NULL, matter TEXT NOT NULL, state TEXT NOT NULL,
  revision TEXT NOT NULL, author TEXT NOT NULL, motif TEXT NOT NULL DEFAULT '', at TEXT NOT NULL);
'''
STATES = {'a_examiner': 'À examiner', 'approuve': 'Approuvé', 'refuse': 'Refusé', 'reporte': 'Reporté', 'remplace': 'Remplacé', 'perime': 'Périmé'}
CLASSES = {'identite': 'Identité des parties', 'classement': 'Classement des documents', 'informations': 'Informations proposées',
           'contradiction': 'Contradiction', 'lacune': 'Information nécessaire', 'relecture': 'Modifications proposées',
           'livrable': 'Projet à relire', 'strategie': 'Choix stratégique', 'engagement': 'Envoi ou engagement à valider',
           # 5.6.57 (A09) : décisions attendues par l'agent et projets à relire d'Aujourd'hui, désormais dans la même file
           'decision_agent': 'Décision attendue par l’agent', 'projet': 'Projet à relire',
           'securite': 'Sécurité de l’IA', 'savoir': 'Savoir-faire à approuver',   # 5.6.59
           'decision': 'Proposition à décider',                # 5.6.58 : propositions de facturation (sans cette classe, la file disparaissait)
           'echec_agent': 'Analyse interrompue',               # 5.6.61 : missions interrompues, regroupées et repliées en fin de file
           'courriel': 'Courriel à relire', 'note_interne': 'Note interne'}   # 5.6.66 : rubriques séparées
FILTERS = {'decider': ('identite', 'classement', 'informations', 'contradiction', 'lacune', 'strategie', 'engagement', 'decision_agent', 'decision', 'securite', 'savoir'),
           'relire': ('relecture', 'livrable', 'projet', 'courriel', 'note_interne'),
           'echecs': ('echec_agent',),
           'courriels': ('courriel',), 'documents': ('relecture', 'livrable', 'projet'), 'notes': ('note_interne',)}
# 5.6.66 (retour du cabinet du 20 octobre 2026 : « ça mélange un peu tout ») : sur Aujourd'hui, une rubrique par nature, les courriels
# à relire et envoyer en premier ; les notes internes à part, avec « Ranger dans le dossier ».
TODAY_GROUPS = (('courriels', 'Courriels à relire et envoyer', 12), ('decider', 'À décider', 10), ('documents', 'Documents à examiner', 8),
                ('notes', 'Notes internes', 6))
NOT_BATCHABLE = ('strategie', 'livrable', 'lacune', 'engagement', 'decision_agent', 'securite', 'savoir', 'echec_agent', 'courriel', 'note_interne')
# 5.6.64 : sur Aujourd'hui, seules les décisions et relectures du jour ; la tenue des fiches de dossiers (identités, classements,
# informations proposées, lacunes, relectures de modifications) et les points d'avancement client se traitent depuis chaque dossier
# (résumé cliquable en bas de la file). Les projets à relire de plus de 14 jours n'y figurent plus (toujours visibles dans le dossier).
CURATION = frozenset(('identite', 'classement', 'informations', 'lacune', 'relecture'))
TODAY_DAYS = 14


def today_split(every, now=None):
    """(éléments du jour, fiches à revoir, points client, anciens projets) pour la vue d'Aujourd'hui."""
    limit = ((now or datetime.now(timezone.utc)) - timedelta(days=TODAY_DAYS)).isoformat()
    keep, curation, client, old = [], [], [], []
    for it in every:
        if it['class'] in CURATION:
            curation.append(it)
        elif str(it.get('object', '')).startswith('suivi:'):
            client.append(it)
        elif it['class'] in ('projet', 'livrable', 'note_interne') and it.get('when') and str(it['when']) < limit:
            old.append(it)
        else:
            keep.append(it)
    return keep, curation, client, old


# 5.6.61 : ordre de lecture — ce que l'avocat attend (prioritaire), puis ce qui est prêt à relire, puis les décisions, enfin les échecs automatiques
RANK = {'courriel': -1, 'projet': 0, 'livrable': 0, 'relecture': 1, 'note_interne': 2, 'echec_agent': 9}     # choix stratégiques et engagements : jamais en validation regroupée


def _now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def _sha(*parts):
    return hashlib.sha256('|'.join(str(p) for p in parts).encode('utf-8')).hexdigest()[:20]


def ensure_schema(desk):
    desk.db.executescript(SCHEMA)


def _deadline_reason(desk, mid):
    """Priorité traçable : échéance confirmée dans les sept jours."""
    try:
        row = desk.db.execute("SELECT due,rule_id FROM deadlines450 WHERE matter=? AND status IN ('confirmee','manuel') AND due<>'' AND due>=? AND due<=? "
                              "ORDER BY due LIMIT 1", (mid, date.today().isoformat(), (date.today() + timedelta(days=7)).isoformat())).fetchone()
    except Exception:   # noqa: BLE001
        return ''
    return ('Échéance confirmée le %s' % row['due']) if row else ''


def _items_for(desk, prefix, m):
    from . import modele5651, memoire5651
    mid = m['id']
    name = matter_display(m)
    q = lambda vue, extra='': prefix + '/dossier?id=' + mid + ('&vue=' + vue if vue else '') + extra
    items = []
    modele5651.ensure_schema(desk)
    memoire5651.ensure_schema(desk)
    for c in modele5651.candidates(desk, mid):
        items.append({'class': 'identite', 'object': 'parties:%s:%s' % (c['a']['id'], c['b']['id']), 'revision': _sha(c['a']['version'], c['b']['version']),
                      'title': 'Même personne ? « %s » et « %s »' % (c['a']['display_name'], c['b']['display_name']), 'consequence': c['reason'],
                      'href': q('', '#d51-parties'), 'choices': ('fusionner', 'separer'), 'a': c['a']['id'], 'b': c['b']['id']})
    to_class = desk.db.execute("SELECT COUNT(*),COALESCE(MAX(updated),'') FROM d51_documents WHERE matter=? AND nature='a_classer' AND nature_source<>'avocat'",
                               (mid,)).fetchone()
    if to_class[0]:
        items.append({'class': 'classement', 'object': 'documents:a_classer', 'revision': _sha(*to_class), 'title': '%d document(s) à classer' % to_class[0],
                      'consequence': 'Nature incertaine : ces documents n’apparaissent ni dans Pièces ni dans Procédure.', 'href': q('pieces', '&filtre=a_classer'),
                      'choices': ('ouvrir',)})
    proposed = desk.db.execute("SELECT COUNT(*),COALESCE(MAX(updated),'') FROM d51_assertions WHERE matter=? AND review='propose' AND relevance<>'hors_sujet'",
                               (mid,)).fetchone()
    if proposed[0]:
        items.append({'class': 'informations', 'object': 'informations:propose', 'revision': _sha(*proposed),
                      'title': '%d information(s) proposée(s) à examiner' % proposed[0],
                      'consequence': 'Utilisées avec leur attribution et la mention « proposée, non contrôlée » tant que vous ne les avez pas examinées.',
                      'href': q('faits'), 'choices': ('ouvrir',)})
    for c in memoire5651.changes(desk, mid, 0, False, 50):
        if c['kind'] == 'montant_modifie' and not c['acknowledged']:
            items.append({'class': 'contradiction', 'object': 'changement:%d' % c['id'], 'revision': c['version'], 'title': c['title'],
                          'consequence': c['detail'].get('consequence', ''), 'href': q('faits'), 'choices': ('approuver', 'reporter'), 'change': c['id']})
    for g in memoire5651.gaps(desk, mid):
        if g['blocking'] == 'engagement':
            items.append({'class': 'lacune', 'object': 'lacune:' + g['id'], 'revision': _sha(g['updated']), 'title': 'Information nécessaire : ' + g['expected'],
                          'consequence': memoire5651.BLOCKING['engagement'], 'href': q('travaux'), 'choices': ('ouvrir', 'reporter'), 'gap': g['id']})
    try:
        from .relecture5651 import pending_sessions
        for s in pending_sessions(desk, mid):
            items.append({'class': 'relecture', 'object': 'relecture:' + s['session'], 'revision': s['revision'],
                          'title': '%d modification(s) proposée(s) dans « %s »' % (s['count'], s['name']),
                          'consequence': 'Rien n’est appliqué au fichier sans votre décision, passage par passage.', 'href': q('travaux', '&relecture=' + s['session']),
                          'choices': ('ouvrir',)})
    except Exception:   # noqa: BLE001
        pass
    try:
        for r in desk.db.execute("SELECT id,title,updated FROM missions5614 WHERE matter=? AND validation='a_decider'", (mid,)).fetchall():
            items.append({'class': 'livrable', 'object': 'mission:' + r['id'], 'revision': r['updated'], 'when': r['updated'], 'title': 'Projet prêt à relire : ' + r['title'],
                          'consequence': 'Valider ou demander une correction depuis la mission ; aucun envoi.', 'href': prefix + '/missions-complexes?id=' + r['id'],
                          'choices': ('ouvrir',)})
    except Exception:   # noqa: BLE001
        pass
    try:
        since = (datetime.now(timezone.utc) - timedelta(days=14)).isoformat()
        for r in desk.db.execute("SELECT id,request,path,updated FROM docreq520 WHERE matter=? AND status='cree' AND updated>=? ORDER BY updated DESC LIMIT 20",
                                 (mid, since)).fetchall():
            items.append({'class': 'livrable', 'object': 'document:' + r['id'], 'revision': r['updated'], 'when': r['updated'], 'title': 'Document produit à relire : ' + r['request'][:120],
                          'consequence': 'Marquer comme relu le retire de toutes les vues.', 'href': q('travaux'), 'choices': ('relu', 'reporter')})
    except Exception:   # noqa: BLE001
        pass
    reason = _deadline_reason(desk, mid)
    for it in items:
        it['matter'] = mid
        it['matter_name'] = name
        it['key'] = _sha(mid, it['object'], it['class'])
        it['priority'] = 'haute' if (reason or it['class'] == 'lacune') else 'normale'
        it['priority_reason'] = reason or ('Bloque un engagement' if it['class'] == 'lacune' else '')
    return items


def _global_items(desk, prefix, actor, role):
    """5.6.52 : éléments du cabinet sans dossier — comptes rendus de routines (R06) et opérations de connexion à valider (I…)."""
    found = []
    for module in ('routines5652', 'connexions5652', 'facturation5653', 'file5657', 'parcours5657', 'bouclier5659', 'savoir5659'):
        try:
            mod = __import__('agent.' + module, fromlist=['queue_items'])
            found += mod.queue_items(desk, prefix, actor, role)
        except Exception:   # noqa: BLE001 — module ou table absents : aucune entrée
            continue
    for it in found:
        it.setdefault('matter', '')
        it.setdefault('matter_name', 'Cabinet')
        it['key'] = _sha(it['matter'], it['object'], it['class'])
        it['priority'] = it.get('priority', 'normale')
        it['priority_reason'] = it.get('priority_reason', '')
    return found


def items(desk, prefix='', matter=None, view='', hidden=frozenset(), include_closed=False, actor='cabinet', role='administrateur'):
    """Éléments actifs (à examiner, ou reportés arrivés à échéance) ; un élément décidé sur sa révision courante disparaît partout."""
    ensure_schema(desk)
    matters = [m for m in load_matters(desk.c) if str(m['id']) not in hidden and (matter is None or str(m['id']) == str(matter))]
    if matter is None:   # seuls les dossiers qui ont quelque chose à décider sont parcourus
        active = set()
        since = (datetime.now(timezone.utc) - timedelta(days=14)).isoformat()
        for sql, params in (("SELECT DISTINCT matter FROM d51_party_candidates WHERE state='propose'", ()),
                            ("SELECT DISTINCT matter FROM d51_documents WHERE nature='a_classer'", ()),
                            ("SELECT DISTINCT matter FROM d51_assertions WHERE review='propose'", ()),
                            ("SELECT DISTINCT matter FROM d51_changes WHERE kind='montant_modifie' AND acknowledged=0", ()),
                            ("SELECT DISTINCT matter FROM d51_gaps WHERE state='ouverte'", ()),
                            ("SELECT DISTINCT matter FROM d51_proposals WHERE state IN ('proposee','modifiee','acceptee')", ()),
                            ("SELECT DISTINCT matter FROM missions5614 WHERE validation='a_decider'", ()),
                            ("SELECT DISTINCT matter FROM docreq520 WHERE status='cree' AND updated>=?", (since,))):
            try:
                active |= {r[0] for r in desk.db.execute(sql, params)}
            except Exception:   # noqa: BLE001 — table absente
                pass
        matters = [m for m in matters if str(m['id']) in active]
    out = []
    now = _now()
    batches = []
    for m in matters:
        try:
            batches.append(_items_for(desk, prefix, m))
        except Stop:
            continue
    if matter is None or matter == '':
        batches.append(_global_items(desk, prefix, actor, role))
    else:                                                      # 5.6.57 (A09) : mêmes objets dans la page du dossier
        batches.append([g for g in _global_items(desk, prefix, actor, role) if str(g.get('matter') or '') == str(matter)])
    for found in batches:
        for it in found:
            if view and it['class'] not in FILTERS.get(view, ()):
                continue
            row = desk.db.execute('SELECT * FROM d51_queue WHERE key=?', (it['key'],)).fetchone()
            if row and row['revision'] == it['revision']:
                if row['state'] in ('approuve', 'refuse', 'remplace'):
                    continue
                if row['state'] == 'reporte' and row['until'] > now:
                    it['state'] = 'reporte'
                    if not include_closed:
                        continue
            it.setdefault('state', 'a_examiner')
            out.append(it)
    out.sort(key=lambda i: (i['priority'] != 'haute', RANK.get(i['class'], 5), i['matter_name'], i['class']))
    return out


def decide(desk, owner, key, state, revision, motif='', days=1, prefix='', hidden=frozenset(), expected_matter=None, actor=None, role='administrateur'):
    """Décision sur la révision affichée ; si l'objet a changé, refus « version en conflit » et élément périmé."""
    ensure_schema(desk)
    if state not in ('approuve', 'refuse', 'reporte'):
        raise Stop('decision_file_invalide')
    current = next((i for i in items(desk, prefix, None, '', hidden, include_closed=True, actor=actor or owner, role=role) if i['key'] == key), None)
    if current is None or (expected_matter is not None and current['matter'] != expected_matter):
        raise Stop('element_absent')                     # 5.6.52 (F01) : clé d'un autre dossier refusée comme absente
    if str(revision) != str(current['revision']):
        _store(desk, key, current['matter'], current['class'], 'perime', str(revision), owner, 'révision dépassée')
        raise Stop('version_en_conflit')
    until = ''
    if state == 'reporte':
        until = (datetime.now(timezone.utc) + timedelta(days=max(1, min(int(days or 1), 30)))).isoformat(timespec='seconds')
    _store(desk, key, current['matter'], current['class'], state, current['revision'], owner, motif, until)
    if current['class'] == 'contradiction' and state == 'approuve':
        desk.db.execute('UPDATE d51_changes SET acknowledged=1 WHERE id=?', (current['change'],))
        desk.db.commit()
    if current.get('occurrence') and state == 'approuve':   # 5.6.52 : compte rendu de routine relu
        desk.db.execute('UPDATE routines5652_occ SET reviewed=1 WHERE id=?', (current['occurrence'],))
        desk.db.commit()
    return {'key': key, 'state': state, 'until': until}


def decide_batch(desk, owner, entries, state, prefix='', hidden=frozenset(), expected_matter=None):
    """Validation regroupée : éléments homogènes (même classe), hors choix stratégiques et engagements ; chaque révision est contrôlée."""
    if not isinstance(entries, list) or not entries:
        raise Stop('selection_vide')
    current = {i['key']: i for i in items(desk, prefix, None, '', hidden, include_closed=True)}
    classes = {current[e['key']]['class'] for e in entries if isinstance(e, dict) and e.get('key') in current}
    if len(classes) != 1:
        raise Stop('selection_heterogene')
    if classes & set(NOT_BATCHABLE):
        raise Stop('selection_non_regroupable')
    done, conflicts = [], []
    for e in entries:
        try:
            done.append(decide(desk, owner, str(e.get('key')), state, str(e.get('revision')), str(e.get('motif') or ''), prefix=prefix, hidden=hidden,
                               expected_matter=expected_matter)['key'])
        except Stop as ex:
            conflicts.append({'key': e.get('key'), 'error': str(ex)})
    return {'decided': done, 'conflicts': conflicts}


def _store(desk, key, mid, cls, state, revision, author, motif='', until=''):
    now = _now()
    desk.db.execute('INSERT OR REPLACE INTO d51_queue(key,matter,item_class,state,revision,author,motif,until,at) VALUES(?,?,?,?,?,?,?,?,?)',
                    (key, mid, cls, state, revision, author, str(motif)[:500], until, now))
    desk.db.execute('INSERT INTO d51_queue_log(key,matter,state,revision,author,motif,at) VALUES(?,?,?,?,?,?,?)', (key, mid, state, revision, author, str(motif)[:500], now))
    desk.db.commit()


def history(desk, matter, limit=50):
    ensure_schema(desk)
    return [dict(r) for r in desk.db.execute('SELECT * FROM d51_queue_log WHERE matter=? ORDER BY id DESC LIMIT ?', (matter, int(limit)))]


def html(desk, prefix, matter=None, view='', hidden=frozenset(), limit=30, title=True, actor='cabinet', role='administrateur'):
    """Cartes courtes (dossier, action attendue, motif, choix) ; filtres avec compteurs ; analyses automatiques interrompues repliées.

    5.6.61 (retour du 18 octobre 2026) : titres lisibles (jamais l'instruction interne de l'agent), motif réel d'un échec, Relancer /
    Écarter dans la carte, doublons regroupés, projets à relire avant les décisions, échecs automatiques en fin de file."""
    from html import escape as e
    from urllib.parse import quote as _q
    every = items(desk, prefix, matter, '', hidden, actor=actor, role=role)
    summary = ''
    if not matter:   # 5.6.64 : vue « du jour » sur Aujourd'hui
        every, curation, client, old = today_split(every)
        summary = _today_summary(prefix, curation, client, old)
    rows = [it for it in every if not view or it['class'] in FILTERS.get(view, ())]
    base = (prefix + '/dossier?id=' + matter + '&') if matter else (prefix + '/aujourdhui?')
    keys = ('', 'courriels', 'decider', 'documents', 'notes', 'echecs') if not matter else ('', 'relire', 'decider', 'echecs')
    counts = {k: sum(1 for it in every if not k or it['class'] in FILTERS[k]) for k in set(keys) | {view} if k in FILTERS or not k}
    labels = {'': 'Tout', 'relire': 'À relire', 'decider': 'À décider', 'echecs': 'Interrompus', 'courriels': 'Courriels', 'documents': 'Documents',
              'notes': 'Notes internes'}
    tabs = ''.join('<a href="%s"%s>%s <span class="q51-n">%d</span></a>' % (
        e(base + ('file=' + k if k else ''), quote=True), ' aria-current="true"' if k == view else '', e(labels[k]), counts[k])
        for k in keys if k in counts and (k in ('', view) or counts[k]))

    def card(it):
        buttons = []
        for c in it['choices']:
            if c == 'ouvrir':
                buttons.append('<a class="ax-btn ghost" href="%s">%s</a>' % (e(it['href'], quote=True), 'Détail' if it['class'] == 'echec_agent' else 'Ouvrir'))
            elif c in ('relancer', 'ecarter'):
                buttons.append('<form class="m5-form q51-inline" data-api="m5661/file/missions" data-reload><input type="hidden" name="ids" value="%s">'
                               '<input type="hidden" name="action" value="%s"><button class="ax-btn%s" type="submit">%s</button></form>' % (
                                   e(','.join(it['missions']), quote=True), 'resume' if c == 'relancer' else 'cancel', '' if c == 'relancer' else ' ghost',
                                   'Relancer' if c == 'relancer' else 'Écarter'))
            elif c == 'exclure':   # 5.6.68 : plus de mission automatique pour ce dossier (dossier de dépôt, scanner…)
                buttons.append('<form class="m5-form q51-inline" data-api="m5668/proactivite/exclure" data-reload data-confirm="Ne plus proposer de '
                               'mission automatique pour ce dossier ? Les missions en attente seront écartées ; réversible dans Paramètres › Proactivité.">'
                               '<input type="hidden" name="matter" value="%s"><button class="ax-btn ghost" type="submit">Ne plus proposer pour ce dossier'
                               '</button></form>' % e(it['matter'], quote=True))
            elif c == 'ranger':   # 5.6.66 : note interne déposée comme brouillon → document Word du dossier
                buttons.append('<form class="m5-form q51-inline" data-api="m5661/file/ranger-notes" data-reload><input type="hidden" name="items" value="%s">'
                               '<button class="ax-btn" type="submit">Ranger dans le dossier</button></form>' % e(it.get('item', ''), quote=True))
            elif c in ('fusionner', 'separer'):
                buttons.append('<form class="m5-form q51-inline" data-api="m5651/partie/%s" data-reload><input type="hidden" name="matter" value="%s">'
                               '<input type="hidden" name="a" value="%s"><input type="hidden" name="b" value="%s"><button class="ax-btn%s" type="submit">%s</button></form>'
                               % ('fusion' if c == 'fusionner' else 'separer', e(it['matter'], quote=True), e(it['a'], quote=True), e(it['b'], quote=True),
                                  '' if c == 'fusionner' else ' ghost', 'Même personne' if c == 'fusionner' else 'Personnes distinctes'))
            elif c in ('approuver', 'relu', 'reporter'):
                state = {'approuver': 'approuve', 'relu': 'approuve', 'reporter': 'reporte'}[c]
                label = {'approuver': 'Pris en compte', 'relu': 'Relu', 'reporter': 'Reporter'}[c]
                buttons.append('<form class="m5-form q51-inline" data-api="%s" data-reload><input type="hidden" name="matter" value="%s">'
                               '<input type="hidden" name="key" value="%s"><input type="hidden" name="revision" value="%s"><input type="hidden" name="state" value="%s">'
                               '<button class="ax-btn%s" type="submit">%s</button></form>' % ('m5651/file/decider' if it['matter'] else 'm5652/file/decider', e(it['matter'], quote=True), e(it['key'], quote=True),
                                                                                             e(str(it['revision']), quote=True), state,
                                                                                             ' ghost' if state == 'reporte' else '', label))
        where = e(it['matter_name'])
        if it['matter'] and not matter:
            where = '<a href="%s">%s</a>' % (e(prefix + '/dossier?id=' + _q(str(it['matter']), safe=''), quote=True), where)
        return ('<li class="q51-item q51-%s"><div class="q51-head"><span class="q51-class">%s</span>%s<span class="q51-matter">%s</span></div>'
                '<p class="q51-title">%s</p>%s%s<div class="q51-actions">%s</div></li>' % (
                    e(it['class']), e(CLASSES.get(it['class'], 'À examiner')),
                    ' <span class="q51-prio" title="%s">Prioritaire</span>' % e(it['priority_reason'], quote=True) if it['priority'] == 'haute' else '',
                    where, e(it['title']),
                    '<p class="q51-sub">%s</p>' % e(it['action']) if it.get('action') else '',
                    '<p class="q51-why">%s</p>' % e(it['consequence']) if it['consequence'] else '', ''.join(buttons)))
    if not matter and view in ('', 'relire'):
        return _today_html(desk, prefix, every, rows, card, tabs, summary, title, limit)
    main = [it for it in rows if it['class'] != 'echec_agent' or it['priority'] == 'haute' or view == 'echecs']
    folded = [it for it in rows if it not in main]
    cards = [card(it) for it in main[:limit]]
    more = '<p class="ax-muted">%d autre(s) élément(s).</p>' % (len(main) - limit) if len(main) > limit else ''
    if folded:
        more += ('<details class="q51-folded"><summary>Analyses automatiques interrompues (%d) — rien n’est perdu ; relancez ou écartez</summary>'
                 '<ul class="q51-list">%s</ul></details>') % (len(folded), ''.join(card(it) for it in folded[:limit]))
    head = ('<h2 id="q51-t">À décider / À relire</h2>' if title else '') + '<nav class="q51-filters" aria-label="Filtres de la file">%s</nav>' % tabs
    if not rows:
        return '<section class="q51" aria-labelledby="q51-t">%s<p class="ax-muted">Rien à décider ni à relire pour l’instant.</p>%s</section>' % (head, summary)
    return '<section class="q51" aria-labelledby="q51-t">%s<ul class="q51-list">%s</ul>%s%s</section>' % (
        head, ''.join(cards) or '<li class="ax-muted">Rien d’autre à décider ni à relire.</li>', more, summary)


def _today_html(desk, prefix, every, rows, card, tabs, summary, title, limit):
    """5.6.66 : Aujourd'hui — une rubrique par nature, courriels d'abord ; analyses interrompues et tenue des dossiers repliées."""
    from html import escape as e
    parts = []
    grouped = {c for key, _, _ in TODAY_GROUPS for c in FILTERS[key]} | {'echec_agent'}
    for key, label, size in TODAY_GROUPS:
        group = [it for it in rows if it['class'] in FILTERS[key]
                 or (key == 'decider' and it['class'] not in grouped)]   # type sans rubrique : jamais perdu, rangé dans « À décider »
        if not group:
            continue
        extra = ''
        if key == 'notes':
            items = ','.join(it.get('item', '') for it in group if it.get('item'))
            if items:
                extra = ('<form class="m5-form q51-inline" data-api="m5661/file/ranger-notes" data-reload><input type="hidden" name="items" value="%s">'
                         '<button class="ax-btn ghost" type="submit">Tout ranger dans les dossiers</button><p role="status" class="m5-status"></p></form>'
                         % e(items[:4000], quote=True))
        more = ('<p class="ax-muted"><a href="%s">%d autre(s) — tout voir</a></p>' % (e(prefix + '/aujourdhui?file=' + key, quote=True), len(group) - size)
                if len(group) > size else '')
        parts.append('<div class="q51-group q51-g-%s"><h3>%s <span class="q51-n">%d</span></h3>%s<ul class="q51-list">%s</ul>%s</div>' % (
            key, e(label), len(group), extra, ''.join(card(it) for it in group[:size]), more))
    folded = [it for it in rows if it['class'] == 'echec_agent']
    if folded:
        parts.append('<details class="q51-folded"><summary>Analyses automatiques interrompues (%d) — rien n’est perdu ; relancez ou écartez</summary>'
                     '<ul class="q51-list">%s</ul></details>' % (len(folded), ''.join(card(it) for it in folded[:limit])))
    head = ('<h2 id="q51-t">À décider / À relire</h2>' if title else '') + '<nav class="q51-filters" aria-label="Filtres de la file">%s</nav>' % tabs
    body = ''.join(parts) or '<p class="ax-muted">Rien à décider ni à relire pour l’instant.</p>'
    return '<section class="q51 q51-today" aria-labelledby="q51-t">%s%s%s</section>' % (head, body, summary)


def _today_summary(prefix, curation, client, old):
    """Résumé de ce qui ne figure pas sur Aujourd'hui, par dossier, avec un lien vers chaque dossier."""
    from html import escape as e
    from urllib.parse import quote as _q

    def by_matter(group, view):
        counts = {}
        for it in group:
            if it.get('matter'):
                key = (it['matter'], it.get('matter_name') or it['matter'])
                counts[key] = counts.get(key, 0) + 1
        top = sorted(counts.items(), key=lambda kv: -kv[1])[:10]
        links = ', '.join('<a href="%s">%s</a> (%d)' % (e(prefix + '/dossier?id=' + _q(str(m), safe='') + '&vue=' + view, quote=True), e(name), n)
                          for (m, name), n in top)
        return links + (' …' if len(counts) > 10 else '')
    parts = []
    if curation:
        parts.append('<li><strong>%d point(s) de fiches de dossiers</strong> (identités, classements, informations proposées) — à revoir depuis '
                     'chaque dossier, quand vous y travaillez : %s</li>' % (len(curation), by_matter(curation, 'vue')))
    if client:
        parts.append('<li><strong>%d point(s) d’avancement client proposé(s)</strong> — onglet Travaux du dossier : %s</li>' % (
            len(client), by_matter(client, 'travaux')))
    if old:
        parts.append('<li><strong>%d projet(s) de plus de %d jours</strong> non relus — toujours visibles dans leur dossier : %s</li>' % (
            len(old), TODAY_DAYS, by_matter(old, 'travaux')))
    if not parts:
        return ''
    return ('<details class="q51-folded"><summary>Hors de la vue du jour (%d) — tenue des dossiers, sans urgence</summary><ul class="d51-list">%s</ul>'
            '</details>') % (len(curation) + len(client) + len(old), ''.join(parts))
