"""5.6.34 (B16, U06) : « Détail » ouvre la demande précise ; « Annuler » est une opération métier durable, distincte d'une pause.

- Descripteur commun d'une décision : type, identifiant de demande, mission liée, travail lié, état, dossier, actions permises ; les
  identifiants restent côté serveur et le droit sur l'objet est contrôlé (propriétaire de la mission ou administrateur).
- Annuler une demande de document : sans travail actif → état terminal « annule », exclu des reprises ; travail en file → demande et
  travail annulés ensemble ; travail en cours → « arrêt demandé », puis état terminal quand le worker s'arrête ; un résultat déjà
  déposé est conservé et annoncé (« Résultat déjà produit conservé »). Une annulation répétée renvoie le même résultat.
- Annuler une mission ordinaire en erreur, en attente de décision ou active (et non plus « pause », refusée hors état actif).
- Historique : qui annule, quand, motif facultatif ; rien n'est supprimé (pièces, rendez-vous, messages envoyés restent en place).
- La page de détail est en lecture seule : sa consultation ne modifie aucun état.
"""
from datetime import datetime, timezone
from html import escape as e
import json
from pathlib import PurePosixPath
import re
from urllib.parse import urlencode

from .common import Stop, load_matters, matter_display

DETAIL_KINDS = ('docreq', 'mission', 'document', 'plan', 'commitment', 'rule')
DOC_STATUS = {'en_file': 'En file', 'en_cours': 'En rédaction', 'dossier_a_choisir': 'Dossier à préciser', 'echec': 'En échec', 'cree': 'Document produit',
              'depot_en_cours': 'Dépôt en cours', 'annule': 'Annulée', 'annulation_demandee': 'Arrêt demandé'}


def schema(desk):
    desk.db.executescript('''CREATE TABLE IF NOT EXISTS decision_cancellations5634(kind TEXT NOT NULL, ident TEXT NOT NULL, by TEXT NOT NULL,
        at TEXT NOT NULL, motive TEXT NOT NULL DEFAULT '', outcome TEXT NOT NULL, PRIMARY KEY(kind, ident));''')


def detail_url(prefix, kind, ident):
    return prefix + '/decision?' + urlencode({'type': kind, 'id': ident})


def _record(desk, kind, ident, owner, motive, outcome):
    schema(desk)
    desk.db.execute('INSERT OR REPLACE INTO decision_cancellations5634 VALUES(?,?,?,?,?,?)',
                    (kind, ident, owner, desk.now(), str(motive or '')[:300], outcome))
    desk.db.commit()
    desk.audit('decision_annulee5634', {'kind': kind, 'id': ident, 'outcome': outcome})


def _job_cancel(desk, job_id):
    """Annule le travail lié : 'cancelled' (en file), 'cancel_requested' (en cours) ou '' (déjà terminé)."""
    if not job_id:
        return ''
    try:
        return desk.cancel_job(job_id)['status']
    except Stop:
        return ''


def cancel_docreq(desk, rid, owner='cabinet', motive=''):
    from .docrequest520 import ensure_schema
    ensure_schema(desk)
    row = desk.db.execute('SELECT * FROM docreq520 WHERE id=?', (str(rid or ''),)).fetchone()
    if not row:
        raise Stop('demande_document_absente')
    if row['status'] == 'annule':
        return {'state': 'annule', 'message': 'Demande annulée.'}
    if row['status'] == 'cree':
        return {'state': 'deja_produit', 'message': 'Résultat déjà produit conservé : le document reste dans le dossier.'}
    job = _job_cancel(desk, row['job_id']) if row['status'] in ('en_file', 'en_cours', 'depot_en_cours', 'annulation_demandee') else ''
    state = 'annulation_demandee' if job == 'cancel_requested' else 'annule'
    desk.db.execute('UPDATE docreq520 SET status=?, updated=? WHERE id=?', (state, desk.now(), row['id']))
    if _has_missions(desk):
        desk.db.execute("UPDATE missions_v567 SET state=?,updated=? WHERE ref=? AND state NOT IN ('cancelled','prepared','verified','answered')",
                        ('cancel_requested' if state == 'annulation_demandee' else 'cancelled', desk.now(), 'docreq:' + row['id']))
    desk.db.commit()
    _record(desk, 'docreq', row['id'], owner, motive, state)
    return {'state': state, 'message': 'Arrêt demandé : le travail en cours s’arrête après son étape ; un fichier déjà déposé sera conservé.'
            if state == 'annulation_demandee' else 'Demande annulée. Les fichiers déjà créés sont conservés.'}


def _has_missions(desk):
    try:
        desk.db.execute('SELECT 1 FROM missions_v567 LIMIT 1')
        return True
    except Exception:   # noqa: BLE001
        return False


def cancel_mission(desk, ident, owner='cabinet', admin=False, motive=''):
    from . import missions567
    row = missions567._row(desk, str(ident or ''), owner, admin)   # pylint: disable=protected-access — droit sur l'objet contrôlé
    if row['state'] == 'cancelled':
        return {'state': 'cancelled', 'message': 'Mission annulée.'}
    if row['state'] in ('prepared', 'verified', 'answered'):
        return {'state': 'deja_produit', 'message': 'Résultat déjà produit conservé.'}
    job = _job_cancel(desk, row['job_id']) if row['state'] in missions567.ACTIVE else ''
    state = 'cancel_requested' if job == 'cancel_requested' else 'cancelled'
    desk.db.execute('UPDATE missions_v567 SET state=?,updated=? WHERE id=?', (state, desk.now(), row['id']))
    desk.db.commit()
    if row['ref'].startswith('docreq:'):
        try:
            cancel_docreq(desk, row['ref'][7:], owner, motive)
        except Stop:
            pass
    missions567._event(desk, row['id'], 'cancelled', {'matter': row['matter']})   # pylint: disable=protected-access
    _record(desk, 'mission', row['id'], owner, motive, state)
    return {'state': state, 'message': 'Arrêt demandé : la mission s’arrête après l’étape en cours ; les résultats déjà produits sont conservés.'
            if state == 'cancel_requested' else 'Mission annulée. Les fichiers déjà créés sont conservés.'}


def cancel(desk, kind, ident, owner='cabinet', admin=False, motive=''):
    if kind == 'docreq':
        out = cancel_docreq(desk, ident, owner, motive)
    elif kind == 'mission':
        out = cancel_mission(desk, ident, owner, admin, motive)
    else:
        raise Stop('annulation_non_prise_en_charge')
    out['counts'] = counts(desk, owner)
    return out


def counts(desk, owner='cabinet'):
    try:
        from .aujourdhui5614 import items
        rows = items(desk, owner, '')
        return {'total': len(rows), 'urgent': sum(1 for c in rows if c['urgent'])}
    except Exception:   # noqa: BLE001
        return {'total': None, 'urgent': None}


# ------------------------------------------------------------------------------------------------ détail (lecture seule)
def _job(desk, job_id):
    if not job_id:
        return None
    row = desk.db.execute('SELECT id,kind,status,progress,result,created,started,finished FROM jobs WHERE id=?', (job_id,)).fetchone() \
        if _job_columns(desk) else desk.db.execute('SELECT id,kind,status,progress,result FROM jobs WHERE id=?', (job_id,)).fetchone()
    if not row:
        return None
    out = dict(row)
    try:
        out['result'] = json.loads(out.get('result') or '{}')
    except ValueError:
        out['result'] = {}
    return out


def _job_columns(desk):
    cols = {r[1] for r in desk.db.execute('PRAGMA table_info(jobs)')}
    return {'created', 'started', 'finished'} <= cols


def describe(desk, kind, ident, owner='cabinet', admin=False):
    """Descripteur complet et lecture seule d'une décision (aucune écriture)."""
    labels = {m['id']: matter_display(m) for m in load_matters(desk.c)}
    if kind == 'docreq':
        row = desk.db.execute('SELECT * FROM docreq520 WHERE id=?', (str(ident),)).fetchone()
        if not row:
            raise Stop('demande_document_absente')
        res = json.loads(row['result'] or '{}')
        return {'kind': kind, 'id': row['id'], 'title': 'Document demandé', 'instruction': row['request'], 'matter': row['matter'],
                'matter_label': labels.get(row['matter'], 'Dossier à préciser'), 'state': DOC_STATUS.get(row['status'], row['status']),
                'raw_state': row['status'], 'job': _job(desk, row['job_id']), 'files': [row['path']] if row['path'] else [],
                'sources': [str(s) for s in res.get('sources', [])][:20], 'notes': [str(n) for n in res.get('notes', [])][:20],
                'error': res.get('error', ''), 'created': row['created'], 'updated': row['updated'],
                'actions': ['annuler'] if row['status'] in ('en_file', 'en_cours', 'dossier_a_choisir', 'echec') else [],
                'cancellation': _cancellation(desk, 'docreq', row['id'])}
    if kind == 'mission':
        from . import missions567
        row = missions567._row(desk, str(ident), owner, admin)   # pylint: disable=protected-access
        plan = json.loads(row['plan'] or '{}')
        exceptions = json.loads(row['exceptions'] or '[]')
        job = _job(desk, row['job_id'])
        return {'kind': kind, 'id': row['id'], 'title': 'Mission', 'instruction': row['instruction'], 'matter': row['matter'],
                'matter_label': labels.get(row['matter'], ''), 'state': missions567.LABELS.get(row['state'], row['state']), 'raw_state': row['state'],
                'job': job, 'files': [], 'steps': [str(s) for s in plan.get('steps', [])][:30], 'sources': [],
                'notes': [str(x.get('message', '')) for x in exceptions if isinstance(x, dict)], 'error': (job or {}).get('result', {}).get('erreur', '') if job and job['status'] == 'error' else '',
                'created': row['created'], 'updated': row['updated'],
                'actions': ['annuler'] if row['state'] not in ('cancelled', 'prepared', 'verified', 'answered', 'abstained') else [],
                'cancellation': _cancellation(desk, 'mission', row['id'])}
    if kind == 'document':
        row = desk.db.execute('SELECT r.*,d.name FROM document_runs568 r JOIN document_rules568 d ON d.id=r.rule_id WHERE r.id=?', (str(ident),)).fetchone()
        if not row or (row['owner'] != owner and not admin):
            raise Stop('decision_absente')
        from .web568 import human
        return {'kind': kind, 'id': row['id'], 'title': 'Document reçu à classer', 'instruction': row['name'], 'matter': row['matter'],
                'matter_label': labels.get(row['matter'], 'Dossier à préciser'), 'state': row['state'], 'raw_state': row['state'], 'job': None,
                'files': [row['path']], 'sources': [], 'notes': [human(row['explanation'])] if row['explanation'] else [], 'error': '',
                'created': row['created'] if 'created' in row.keys() else '', 'updated': row['updated'], 'actions': [], 'cancellation': None}
    if kind == 'plan':
        row = desk.db.execute('SELECT * FROM plans_v568 WHERE id=?', (str(ident),)).fetchone()
        if not row or (row['owner'] != owner and not admin):
            raise Stop('decision_absente')
        return {'kind': kind, 'id': row['id'], 'title': 'Suite de mission', 'instruction': row['title'], 'matter': row['matter'],
                'matter_label': labels.get(row['matter'], ''), 'state': row['state'], 'raw_state': row['state'], 'job': None, 'files': [], 'sources': [],
                'notes': [], 'error': '', 'created': '', 'updated': row['updated'], 'actions': [], 'cancellation': None}
    if kind == 'commitment':
        row = desk.db.execute('SELECT * FROM commitments_v568 WHERE id=?', (str(ident),)).fetchone()
        if not row or (row['owner'] != owner and not admin):
            raise Stop('decision_absente')
        return {'kind': kind, 'id': row['id'], 'title': 'Engagement', 'instruction': row['expected'], 'matter': row['matter'],
                'matter_label': labels.get(row['matter'], ''), 'state': row['state'], 'raw_state': row['state'], 'job': None, 'files': [], 'sources': [],
                'notes': [row['condition_text']] if row['condition_text'] else [], 'error': '', 'created': '', 'updated': row['updated'], 'actions': [],
                'cancellation': None}
    if kind == 'rule':
        from .pilote5613 import rule_proposal
        p = rule_proposal(desk, str(ident))
        if not p:
            raise Stop('decision_absente')
        return {'kind': kind, 'id': p['id'], 'title': 'Règle proposée', 'instruction': p.get('instruction', ''), 'matter': p.get('matter', ''),
                'matter_label': labels.get(p.get('matter', ''), ''), 'state': p.get('state', ''), 'raw_state': p.get('state', ''), 'job': None, 'files': [],
                'sources': [], 'notes': [], 'error': '', 'created': '', 'updated': '', 'actions': [], 'cancellation': None}
    raise Stop('type_decision_inconnu')


def _cancellation(desk, kind, ident):
    schema(desk)
    row = desk.db.execute('SELECT * FROM decision_cancellations5634 WHERE kind=? AND ident=?', (kind, ident)).fetchone()
    return dict(row) if row else None


def page(desk, auth, prefix, env, args, shell):
    from .web567 import actor
    from .erreurs5634 import explain, public
    owner, role = actor(env)
    kind, ident = str(args.get('type') or ''), str(args.get('id') or '')
    back = prefix + '/aujourdhui#c569-decisions'
    try:
        if kind not in DETAIL_KINDS or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', ident):
            raise Stop('decision_absente')
        d = describe(desk, kind, ident, owner, role == 'administrateur')
        try:   # 5.6.57 (A10) : point de reprise
            from .reprise5657 import note as _note57
            _note57(desk, owner, '/decision', {'type': kind, 'id': ident})
        except Exception:   # noqa: BLE001
            pass
    except Stop as ex:
        from .web440 import human
        return shell('Détail de la demande', '<h1>Détail de la demande</h1><p>%s</p><p><a class="ax-btn" href="%s">Retour à Aujourd’hui</a></p>' % (
            e(human(str(ex))), e(back, quote=True)), prefix, auth['csrf'], '/aujourdhui')
    job = d.get('job') or {}
    detail = (job.get('result') or {}).get('detail') or {}
    err = ((job.get('result') or {}).get('erreur') if job.get('status') == 'error' else '') or d.get('error')   # même source que le détail technique
    body = '<article class="m5634-detail" data-kind="%s" data-id="%s"><p><a href="%s">← Aujourd’hui</a></p><h1>%s</h1>' % (
        e(kind, quote=True), e(d['id'], quote=True), e(back, quote=True), e(d['title']))
    body += '<dl class="m5634-dl"><dt>Instruction</dt><dd>%s</dd><dt>Dossier</dt><dd>%s</dd><dt>État</dt><dd><strong>%s</strong></dd>%s%s</dl>' % (
        e(d['instruction']).replace('\n', '<br>'), e(d['matter_label'] or 'Non précisé'), e(d['state']),
        ('<dt>Créée le</dt><dd>%s</dd>' % e(str(d['created'])[:16].replace('T', ' '))) if d.get('created') else '',
        ('<dt>Mise à jour</dt><dd>%s</dd>' % e(str(d['updated'])[:16].replace('T', ' '))) if d.get('updated') else '')
    if d.get('steps'):
        body += '<h2>Étapes</h2><ol>%s</ol>' % ''.join('<li>%s</li>' % e(s) for s in d['steps'])
    if job:
        body += '<h2>Avancement</h2><p>%s%s</p>' % (e({'pending': 'En file', 'running': 'En cours', 'done': 'Terminé', 'error': 'En échec',
                                                      'cancelled': 'Annulé', 'cancel_requested': 'Arrêt demandé'}.get(job.get('status'), job.get('status', ''))),
                                                    (' — ' + e(job.get('progress'))) if job.get('progress') else '')
    if d.get('notes'):
        body += '<h2>Éléments manquants et remarques</h2><ul>%s</ul>' % ''.join('<li>%s</li>' % e(n) for n in d['notes'] if n)
    if d.get('files'):
        from .fiche460 import doc_href
        body += '<h2>Documents produits ou concernés</h2><ul>%s</ul>' % ''.join(
            '<li><a href="%s">%s</a></li>' % (e(prefix + doc_href(p, d['matter']), quote=True), e(PurePosixPath(p).name)) for p in d['files'])
    if d.get('sources'):
        body += '<h2>Sources</h2><ul>%s</ul>' % ''.join('<li>%s</li>' % e(s) for s in d['sources'])
    if err:
        body += '<section class="m5634-error" role="alert"><h2>Erreur</h2><p>%s</p>%s</section>' % (
            e(explain(err, detail)), ('<details><summary>Détail technique</summary><pre>%s</pre></details>' % e(json.dumps(public(detail), ensure_ascii=False, indent=1))) if detail else '')
    if d.get('cancellation'):
        c = d['cancellation']
        body += '<p class="m5634-note">Annulation enregistrée le %s par %s%s.</p>' % (e(c['at'][:16].replace('T', ' ')), e(c['by']), (' — motif : ' + e(c['motive'])) if c['motive'] else '')
    if 'annuler' in d['actions']:
        body += ('<div class="m5634-actions"><button type="button" class="ax-btn ghost" data-m34-cancel="%s" data-id="%s" '
                 'data-confirm="Annuler cette demande ? Les fichiers déjà créés seront conservés.">Annuler la demande</button>'
                 '<p class="m5634-help">Annuler abandonne cette demande ; Reporter (depuis Aujourd’hui) y revient plus tard et ne change aucune échéance.</p>'
                 '<p role="status" class="m34-status m5634-status"></p></div>') % (e(kind, quote=True), e(d['id'], quote=True))
    body += '<details class="m5634-tech"><summary>Identifiants techniques</summary><p>Type : %s · identifiant : %s%s</p></details></article>' % (
        e(kind), e(d['id']), (' · travail n° %s' % e(str(job.get('id')))) if job else '')
    return shell('Détail de la demande', body, prefix, auth['csrf'], '/aujourdhui')
