"""Détail d'un travail de l'agent (AxiorHub 5.6.1) : ce qui est traité (document demandé, pièce, courriel, dossier), l'avancement,
le résultat et les liens pour ouvrir le fichier produit ou analysé. S'ouvre depuis « Ce que fait l'agent » du poste de pilotage."""
from html import escape as e
import json
from pathlib import PurePosixPath
from urllib.parse import urlencode

from .common import Stop, clean_path, under

STATUS = {'pending': 'en file', 'running': 'en cours', 'done': 'terminé', 'error': 'bloqué', 'cancelled': 'annulé',
          'cancel_requested': 'annulation demandée'}
# Arguments techniques jamais affichés.
HIDDEN = {'etag', 'hash', 'sha256', 'digest', 'attempt', 'trigger', 'dedupe', 'source_hash', 'csrf'}
ARG_LABELS = {'matter': 'Dossier', 'matter_id': 'Dossier', 'path': 'Fichier', 'key': 'Courriel', 'mail_key': 'Courriel', 'request': 'Demande',
              'kind': 'Type', 'instruction': 'Consigne', 'draft': 'Bordereau', 'order': 'Ordre des pièces', 'source_folder': 'Dossier des pièces',
              'title': 'Titre', 'text': 'Texte', 'query': 'Recherche', 'worker_id': 'Worker'}
EDITABLE = ('.docx', '.odt')


def _fmt(desk, stamp):
    from .cockpit530 import _local
    return _local(desk, stamp, '%H:%M') if stamp else ''


def _mail(desk, key):
    row = None
    try:
        row = desk.db.execute('SELECT subject, sender, received, matter FROM work_items WHERE mail_key=?', (key,)).fetchone()
    except Exception:
        pass
    if row:
        return '« %s » — %s' % (row['subject'] or '(sans objet)', row['sender'] or 'expéditeur inconnu'), row['matter'] or ''
    return 'courriel analysé (objet non conservé)', ''


def _file_actions(prefix, path, matter):
    name = PurePosixPath(path).name
    out = []
    if name.lower().endswith(EDITABLE):
        out.append('<a class="ax-btn ghost c530-small" href="%s">Ouvrir dans l’éditeur</a>' % e(prefix + '/documents/edit?' + urlencode({'path': path, 'matter': matter})))
    out.append('<button type="button" class="ax-btn ghost c530-small" data-open-path="%s" data-open-matter="%s">Ouvrir dans Nextcloud</button>' % (
        e(path, quote=True), e(matter, quote=True)))
    return ' '.join(out)


def _rel(matters, matter, path):
    root = (matters.get(matter) or {}).get('path', '')
    return path[len(root):].lstrip('/') if root and path.startswith(root) else path


def describe(desk, prefix, kind, args):
    """Lignes « ce qui est traité » : [(libellé, valeur HTML)]."""
    from .cockpit530 import _labels, _matters
    labels, matters = _labels(desk), _matters(desk)
    matter = str(args.get('matter') or args.get('matter_id') or '')
    rows = []
    if kind == 'docrequest520' and args.get('request'):
        row = desk.db.execute('SELECT * FROM docreq520 WHERE id=?', (str(args['request']),)).fetchone() if _has(desk, 'docreq520') else None
        if row:
            matter = matter or row['matter']
            rows.append(('Document demandé', e(row['request'])))
            if row['revision_of']:
                rows.append(('Nouvelle version de', e(_rel(matters, row['matter'], row['revision_of']))))
            if row['path']:
                rows.append(('Document produit', '%s<br>%s' % (e(_rel(matters, row['matter'], row['path'])), _file_actions(prefix, row['path'], row['matter']))))
            res = json.loads(row['result'] or '{}')
            if res.get('to_complete'):
                rows.append(('À compléter', e(' ; '.join(res['to_complete'][:8]))))
            if res.get('sources'):
                rows.append(('Sources', e(', '.join(res['sources'][:12]))))
    for key in ('key', 'mail_key'):
        if args.get(key):
            text, mail_matter = _mail(desk, str(args[key]))
            matter = matter or mail_matter
            rows.append(('Courriel', e(text) + ' <a class="ax-btn ghost c530-small" href="%s">Courriels</a>' % e(prefix + '/courriels')))
    if args.get('path'):
        path = str(args['path'])
        rows.append(('Pièce' if kind in ('analyze_deadline450', 'analyze_notice440', 'pieces_scan510') else 'Fichier',
                     '<strong>%s</strong><br><span class="c530-sub">%s</span><br>%s' % (
                         e(PurePosixPath(path).name), e(str(PurePosixPath(_rel(matters, matter, path)).parent)), _file_actions(prefix, path, matter))))
    for k, v in args.items():
        if k in HIDDEN or k in ('matter', 'matter_id', 'path', 'key', 'mail_key', 'request') or v in ('', None, [], {}):
            continue
        if k in ARG_LABELS:
            rows.append((ARG_LABELS[k], e(str(v)[:400])))
    if matter:
        rows.insert(0, ('Dossier', '<a href="%s">%s</a>' % (e(prefix + '/fiche?' + urlencode({'matter': matter})), e(labels.get(matter, matter)))))
    return rows


def _has(desk, table):
    return bool(desk.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone())


def results(desk, prefix, job_id, status, raw_result):
    from .cockpit530 import _matters, _reason
    matters = _matters(desk)
    out = []
    if _has(desk, 'production_deliverables_v420'):
        for r in desk.db.execute('SELECT * FROM production_deliverables_v420 WHERE job_id=? ORDER BY updated DESC LIMIT 5', (job_id,)):
            line = e(r['business_message'] or r['label'])
            for f in json.loads(r['verification'] or '{}').get('files', [])[:4]:
                p = str(f.get('path', ''))
                if p:
                    line += '<br>%s %s' % (e(_rel(matters, r['matter'], p)), _file_actions(prefix, p, r['matter']))
            out.append(line)
    try:
        res = json.loads(raw_result or '{}')
    except ValueError:
        res = {}
    if status == 'error':
        out.append('<span class="c530-warn">%s</span>' % e(_reason(res.get('erreur', 'action_interrompue'))))
    elif isinstance(res, dict):
        for k in ('message', 'summary', 'resume'):
            if isinstance(res.get(k), str) and res[k].strip():
                out.append(e(res[k][:600]))
                break
        if isinstance(res.get('examined'), int):
            out.append('%d courriel(s) examiné(s)' % res['examined'])
    return out


def job_html(desk, prefix, job_id):
    from .web import JOB_LABELS
    from .cockpit530 import MAIL_KINDS
    try:
        jid = int(job_id)
    except (TypeError, ValueError):
        raise Stop('travail_invalide') from None
    row = desk.db.execute('SELECT * FROM jobs WHERE id=?', (jid,)).fetchone()
    if not row:
        raise Stop('travail_invalide')
    keys = row.keys()
    try:
        args = json.loads(row['args'] or '{}')
        args = args if isinstance(args, dict) else {}
    except ValueError:
        args = {}
    title = 'Analyse des nouveaux courriels' if row['kind'] in MAIL_KINDS else JOB_LABELS.get(row['kind'], row['kind'])
    when = []
    if row['created']:
        when.append('demandé %s' % _fmt(desk, row['created']))
    if 'started' in keys and row['started']:
        when.append('commencé %s' % _fmt(desk, row['started']))
    if row['finished']:
        when.append('terminé %s' % _fmt(desk, row['finished']))
    what = describe(desk, prefix, row['kind'], args)
    steps = []
    if _has(desk, 'live_events_v430'):
        steps = [dict(x) for x in desk.db.execute('SELECT at, message FROM live_events_v430 WHERE job_id=? ORDER BY id DESC LIMIT 15', (jid,))][::-1]
    done = results(desk, prefix, jid, row['status'], row['result'])
    retry = ('<button type="button" class="ax-btn" data-retry="%d" data-close-after="1">Relancer</button>' % jid) if row['status'] == 'error' else (
        ('<button type="button" class="ax-btn danger" data-cancel-job="%d">Annuler ce travail</button>' % jid) if row['status'] in ('pending', 'running') else '')
    origin = 'votre demande' if int(row['priority'] if 'priority' in keys and row['priority'] is not None else 50) < 40 else 'automatique'
    body = ('<dl class="c561-dl">%s</dl>' % ''.join('<dt>%s</dt><dd>%s</dd>' % (e(k), v) for k, v in what)) if what else (
        '<p class="c530-sub">Travail général, sans dossier ni fichier particulier.</p>')
    body += ('<h3>Avancement</h3><ol class="c561-steps">%s</ol>' % ''.join('<li><span class="c530-when">%s</span> %s</li>' % (e(_fmt(desk, s['at'])), e(s['message']))
                                                                         for s in steps)) if steps else ''
    body += ('<h3>Résultat</h3><ul>%s</ul>' % ''.join('<li>%s</li>' % x for x in done)) if done else ''
    return ('<div class="c530-drawer-head"><span class="c530-type doc">Travail n° %d</span><h2 id="c530-drawer-title">%s</h2><p class="c530-sub">%s · %s</p>'
            '<p class="c530-meta">%s</p></div><div class="c530-drawer-body">%s</div>'
            '<div class="c530-drawer-foot"><div class="c530-actions">%s<a class="ax-btn ghost" href="%s">File de travail</a>'
            '<button type="button" class="ax-btn ghost" data-close="1">Fermer</button></div></div>') % (
        jid, e(title), e(STATUS.get(row['status'], row['status'])), e(origin), e(' · '.join(when)), body, retry, e(prefix + '/diagnostic'))


def open_url(desk, path, matter):
    """Adresse Nextcloud d'un fichier d'un dossier connu (jamais d'un chemin hors des dossiers du cabinet)."""
    from .cockpit530 import _matters
    matters = _matters(desk)
    path = clean_path(str(path or ''))
    m = matters.get(str(matter or ''))
    if not m or not under(path, m['path']):
        if not any(under(path, x['path']) for x in matters.values() if x.get('path')):
            raise Stop('fichier_hors_dossier')
    from .document_projects import _dav
    return {'url': _dav(desk).file_web_url(path)}
