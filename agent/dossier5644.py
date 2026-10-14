"""5.6.44 : corrections de l'avocat dans la page du dossier.

Les informations proposées par l'IA (parties, juridiction, numéro de RG) peuvent être supprimées, modifiées ou complétées depuis la
page du dossier. Une correction est une couche historisée posée sur les propositions : elle s'applique à l'affichage et reste valable
après une nouvelle analyse (les mêmes propositions restent masquées ou remplacées) ; elle s'annule d'un clic. Supprimer ou modifier
une proposition issue des faits du dossier refuse aussi ce fait : il n'est plus réutilisé dans les rédactions.

Les doublons évidents de parties (même nom, l'un suivi d'une adresse ou d'une précision entre parenthèses) sont fusionnés
automatiquement ; la mention la plus sûre (configuration, validée, puis proposée) est conservée avec toutes ses sources.
"""
from html import escape as e
import re

from .common import Stop, fold, load_matters

SECTIONS = {'parties': 'partie', 'jurisdiction': 'juridiction', 'case_number': 'numéro de RG'}
ACTIONS = ('hide', 'replace', 'add')
RANK = {'configured': 5, 'pinned': 4, 'validated': 4, 'suggested': 2, 'derived': 1}
MAX_VALUE = 300


def norm(value):
    """Clé de comparaison : minuscules sans accents, ponctuation et espaces réduits."""
    return re.sub(r'\s+', ' ', re.sub(r'[^\w]+', ' ', fold(str(value or '')))).strip()


def head(value):
    """Nom sans précision finale : « GGM Gastro GmbH (Weinerpark 16, …) » → « ggm gastro gmbh »."""
    text = str(value or '')
    text = re.split(r'\s+[(\[]|,', text, maxsplit=1)[0]   # jamais sur « - » : « SCI X - Y » reste distinct de « SCI X »
    return norm(text)


def _clean(value):
    value = re.sub(r'\s+', ' ', str(value or '')).strip()
    if len(value) > MAX_VALUE:
        raise Stop('correction_trop_longue')
    return value


def _matter(desk, mid):
    m = next((x for x in load_matters(desk.c) if str(x['id']) == str(mid)), None)
    if not m:
        raise Stop('dossier_absent')
    return m


def corrections(desk, mid, section=None, active=True):
    sql = 'SELECT * FROM corrections5644 WHERE matter=?' + (' AND section=?' if section else '') + (' AND active=1' if active else '') + ' ORDER BY id'
    try:
        return [dict(r) for r in desk.db.execute(sql, (mid, section) if section else (mid,))]
    except Exception:   # pylint: disable=broad-except — base antérieure à la migration 0646
        return []


def dedupe(items):
    """Fusion des doublons évidents de parties (même nom en tête) ; la mention la plus sûre est gardée, sources réunies."""
    out = []
    for item in items:
        h = head(item.get('value') or item.get('title'))
        twin = next((o for o in out if h and head(o.get('value') or o.get('title')) == h), None)
        if twin is None:
            out.append(dict(item))
            continue
        keep, other = (item, twin) if RANK.get(item.get('status'), 0) > RANK.get(twin.get('status'), 0) else (twin, item)
        merged = dict(keep)
        labels = {s.get('label') for s in keep.get('sources', [])}
        merged['sources'] = list(keep.get('sources', [])) + [s for s in other.get('sources', []) if s.get('label') not in labels]
        merged['merged'] = keep.get('merged', 0) + other.get('merged', 0) + 1
        merged['merged_ids'] = [x for x in (keep.get('merged_ids') or [keep.get('id')]) + (other.get('merged_ids') or [other.get('id')]) if x]
        out[out.index(twin)] = merged
    return out


def apply(desk, mid, section, items):
    """Propositions + corrections de l'avocat (suppression, remplacement, ajout), doublons fusionnés pour les parties."""
    items = [dict(i) for i in items if i]
    if section == 'parties':
        items = dedupe(items)
    for c in corrections(desk, mid, section):
        if c['action'] == 'add':
            if section != 'parties':
                items = []
            items.append({'id': '', 'value': c['value'], 'title': c['value'], 'status': 'validated', 'status_label': 'Ajouté par l’avocat',
                          'confidence': 'high', 'sources': [{'label': 'Saisie de l’avocat', 'href': '', 'excerpt': ''}], 'validated': True,
                          'type': 'party' if section == 'parties' else section, 'event_date': '', 'correction_id': c['id']})
            continue
        same = head(c['label'])   # la même information revenue avec une précision (adresse, forme sociale entre parenthèses)
        hits = [i for i in items if c['target'] in (norm(i.get('value')), norm(i.get('title'))) or (same and head(i.get('value')) == same)]
        # Le fait corrigé est refusé (il ne revient donc plus dans les propositions) : la valeur de l'avocat le remplace à sa place, ou
        # en fin de liste s'il n'est plus proposé ; une information unique (juridiction, RG) n'a jamais qu'une valeur.
        place = items.index(hits[0]) if hits else len(items)
        for i in hits:
            items.remove(i)
        if c['action'] == 'replace':
            fixed = {'id': '', 'value': c['value'], 'title': c['value'], 'status': 'validated', 'status_label': 'Corrigé par l’avocat',
                     'confidence': 'high', 'sources': [{'label': 'Correction de l’avocat', 'href': '', 'excerpt': ''}], 'validated': True,
                     'type': 'party' if section == 'parties' else section, 'event_date': '', 'correction_id': c['id']}
            items = [fixed] if section != 'parties' else items[:place] + [fixed] + items[place:]
    for i in items:
        i['key'] = norm(i.get('value') or i.get('title'))
    return items


def single(desk, mid, section, item):
    out = apply(desk, mid, section, [item] if item else [])
    return out[-1] if out else None


def correct(desk, owner, data):
    """Supprimer (hide), modifier (replace) ou ajouter (add) une information du dossier ; historisé et annulable."""
    mid = str(data.get('matter') or '')
    m = _matter(desk, mid)
    section, action = str(data.get('section') or ''), str(data.get('action') or '')
    if section not in SECTIONS or action not in ACTIONS:
        raise Stop('correction_invalide')
    value = _clean(data.get('value'))
    label = _clean(data.get('target'))
    target = norm(label)
    if action in ('hide', 'replace') and not target:
        raise Stop('correction_invalide')
    if action in ('replace', 'add') and not value:
        raise Stop('correction_vide')
    if section == 'parties' and action in ('hide', 'replace') and m.get('client_name') and target in (norm(m['client_name']), head(m['client_name'])):
        raise Stop('partie_de_la_configuration')
    same = desk.db.execute('SELECT id FROM corrections5644 WHERE matter=? AND section=? AND action=? AND target=? AND value=? AND active=1',
                           (mid, section, action, target, value)).fetchone()
    if same:
        return {'id': same['id'], 'message': 'Correction déjà enregistrée.'}
    records = [r for r in str(data.get('record_id') or '').split(',') if r.strip()][:20]
    refused = 0
    if action in ('hide', 'replace'):
        from .facts460 import decide
        for rid in records:   # le fait proposé n'est plus réutilisé dans les rédactions
            try:
                decide(desk, mid, rid.strip(), 'reject', note='Supprimé ou corrigé par l’avocat depuis la page du dossier')
                refused += 1
            except Stop:
                pass
    cur = desk.db.execute('INSERT INTO corrections5644(matter,section,action,target,label,value,record_id,owner,created) VALUES(?,?,?,?,?,?,?,?,?)',
                          (mid, section, action, target, label, value, ','.join(records), owner, desk.now()))
    desk.db.commit()
    desk.audit('dossier5644_correction', {'matter': mid, 'section': section, 'action': action, 'owner': owner, 'faits_refuses': refused})
    what = SECTIONS[section]
    message = {'hide': '« %s » supprimé de ce dossier (%s).' % (label, what), 'replace': '« %s » remplacé par « %s ».' % (label, value),
               'add': '« %s » ajouté (%s).' % (value, what)}[action]
    return {'id': cur.lastrowid, 'message': message + ' Annulable ci-dessous.'}


def undo(desk, owner, data):
    mid = str(data.get('matter') or '')
    try:
        cid = int(data.get('id'))
    except (TypeError, ValueError):
        raise Stop('correction_invalide') from None
    changed = desk.db.execute('UPDATE corrections5644 SET active=0, undone_by=?, undone_at=? WHERE id=? AND matter=? AND active=1',
                              (owner, desk.now(), cid, mid)).rowcount
    desk.db.commit()
    if not changed:
        raise Stop('correction_absente')
    desk.audit('dossier5644_annulation', {'matter': mid, 'id': cid, 'owner': owner})
    return {'message': 'Correction annulée : l’information proposée est de nouveau affichée (un fait refusé reste refusé ; validez-le dans « Faits et procédure » si besoin).'}


# ------------------------------------------------------------------------------------------------ affichage
def _hidden(**values):
    return ''.join('<input type="hidden" name="%s" value="%s">' % (k, e(str(v), quote=True)) for k, v in values.items())


def editor_html(mid, section, item):
    """Commandes d'une information : modifier, supprimer (ou annuler la correction de l'avocat)."""
    if not item:
        return ''
    if item.get('status') == 'configured':
        return '<span class="d44-note">Modifiable dans la configuration du dossier.</span>'
    if item.get('correction_id'):
        return ('<form class="m5-form d44-inline" data-api="m5644/dossier/annuler-correction" data-reload>%s'
                '<button type="submit" class="ax-btn ghost d44-small">Annuler ma correction</button><p role="status" class="m5-status"></p></form>'
                % _hidden(matter=mid, id=item['correction_id']))
    value = item.get('value') or item.get('title') or ''
    ids = ','.join(x for x in (item.get('merged_ids') or [item.get('id')]) if x)
    what = SECTIONS[section]
    return ('<details class="d44-edit"><summary>Corriger</summary>'
            '<form class="m5-form" data-api="m5644/dossier/corriger" data-reload>%s'
            '<label>Texte exact<input name="value" maxlength="%d" required value="%s"></label>'
            '<button type="submit" class="ax-btn d44-small">Enregistrer</button><p role="status" class="m5-status"></p></form>'
            '<form class="m5-form" data-api="m5644/dossier/corriger" data-reload data-confirm="%s">%s'
            '<button type="submit" class="ax-btn danger d44-small">Supprimer cette %s</button><p role="status" class="m5-status"></p></form>'
            '</details>') % (
        _hidden(matter=mid, section=section, action='replace', target=value, record_id=ids), MAX_VALUE, e(value, quote=True),
        e('Supprimer « %s » de ce dossier ? L’information ne sera plus affichée ni réutilisée ; vous pourrez annuler.' % value, quote=True),
        _hidden(matter=mid, section=section, action='hide', target=value, record_id=ids), 'information' if section != 'parties' else 'partie')


def add_html(mid, section):
    label = {'parties': 'Ajouter une partie', 'jurisdiction': 'Indiquer la juridiction', 'case_number': 'Indiquer le numéro de RG'}[section]
    return ('<details class="d44-edit d44-add"><summary>%s</summary><form class="m5-form" data-api="m5644/dossier/corriger" data-reload>%s'
            '<label>%s<input name="value" maxlength="%d" required></label><button type="submit" class="ax-btn d44-small">Ajouter</button>'
            '<p role="status" class="m5-status"></p></form></details>') % (
        e(label), _hidden(matter=mid, section=section, action='add'), e(label), MAX_VALUE)


def removed_html(desk, mid, section):
    """Informations supprimées par l'avocat, rétablissables."""
    rows = [c for c in corrections(desk, mid, section) if c['action'] == 'hide']
    if not rows:
        return ''
    return '<details class="d44-removed"><summary>Supprimé(s) par l’avocat (%d)</summary><ul class="fc-list">%s</ul></details>' % (
        len(rows), ''.join('<li>%s <form class="m5-form d44-inline" data-api="m5644/dossier/annuler-correction" data-reload>%s'
                           '<button type="submit" class="ax-btn ghost d44-small">Rétablir</button><p role="status" class="m5-status"></p></form></li>'
                           % (e(c['label']), _hidden(matter=mid, id=c['id'])) for c in rows))
