"""5.6.36 : Dossiers & GED — chaque pièce devient une donnée exploitable.

- **Données des pièces** : pour chaque pièce relevée (relevés contrôlés 5632/5635) — type de document, date, parties, dates clés,
  montants, textes cités, demandes — chaque élément avec son statut (documenté dans l'extrait cité / à vérifier) ; échéances détectées
  du dossier ; export CSV ; mise à jour par « Analyser les pièces du dossier ».
- **Interrogation du cabinet** : recherche dans les données de toutes les pièces (partie, article, montant, date), filtre par type.
- **Pièces jointes des courriels rangées** dans le dossier identifié (« PJ courriels/AAAA-MM-JJ - expéditeur/ ») : à la demande, par une
  initiative validée, ou automatiquement si l'avocat l'a activé ; sans doublon (empreinte), sans écraser un fichier existant ; types
  courants seulement (PDF, Word, tableur, images, texte).
- Ces données alimentent l'assistant, les initiatives, les grilles d'analyse et le secrétariat juridique, sans ressaisie.
"""
import csv
from datetime import datetime, timezone
import hashlib
from html import escape as e
import io
import json
import re

from .common import Stop, load_matters, matter_display

AUTO = 'ged5636:pj_automatique'
ALLOWED = ('.pdf', '.doc', '.docx', '.odt', '.rtf', '.txt', '.xls', '.xlsx', '.ods', '.csv', '.jpg', '.jpeg', '.png', '.tif', '.tiff', '.heic', '.eml', '.msg')
MAX_ATTACHMENT = 20_000_000   # limite des fichiers créés dans Nextcloud (max_generated_file_bytes)


def schema(desk):
    desk.db.executescript('''
    CREATE TABLE IF NOT EXISTS attachments_filed5636(mail_key TEXT NOT NULL, sha256 TEXT NOT NULL, matter TEXT NOT NULL, path TEXT NOT NULL,
      filed TEXT NOT NULL, PRIMARY KEY(mail_key, sha256));''')


def _labels(desk):
    try:
        return {m['id']: matter_display(m) for m in load_matters(desk.c)}
    except Stop:
        return {}


def _text(item, *keys):
    if isinstance(item, str):
        return item
    if not isinstance(item, dict):
        return ''
    for k in keys:
        if item.get(k):
            return str(item[k])
    return ''


def _status(item):
    return (item.get('statut') if isinstance(item, dict) else '') or 'a_verifier'


def piece_records(desk, mid):
    """Une ligne par pièce relevée avec la méthode courante (dernière version du texte)."""
    from . import synthese5632
    synthese5632.schema(desk)
    rows = desk.db.execute('SELECT path, digest, status, created FROM piece_digests5634 WHERE matter=? AND method=? ORDER BY path, created DESC',
                           (mid, synthese5632.METHOD)).fetchall()
    seen, out = set(), []
    for r in rows:
        if r['path'] in seen:
            continue
        seen.add(r['path'])
        try:
            d = json.loads(r['digest'] or '{}')
        except ValueError:
            d = {}
        out.append({
            'path': r['path'], 'name': r['path'].rsplit('/', 1)[-1], 'status': r['status'], 'type': str(d.get('nature') or ''),
            'date': str(d.get('date_document') or ''),
            'parties': [_text(p, 'nom', 'name', 'texte') for p in d.get('parties') or [] if _text(p, 'nom', 'name', 'texte')],
            'dates': [{'date': str(f.get('date') or ''), 'texte': _text(f, 'texte', 'fait'), 'statut': _status(f)} for f in d.get('faits') or []
                      if isinstance(f, dict) and f.get('date')],
            'montants': [{'montant': _text(m, 'montant'), 'objet': _text(m, 'objet'), 'devise': (m.get('devise') if isinstance(m, dict) else '') or '',
                          'statut': _status(m)} for m in d.get('montants') or []],
            'textes': sorted({_text(x, 'reference', 'texte', 'ref') for x in d.get('references') or [] if _text(x, 'reference', 'texte', 'ref')}),
            'demandes': [_text(x, 'texte') for x in d.get('demandes') or [] if _text(x, 'texte')],
        })
    return out


def deadlines(desk, mid):
    try:
        from .echeances450 import listing
        return listing(desk, mid)
    except Exception:   # noqa: BLE001 — module des échéances indisponible : simplement rien à montrer
        return []


def search_all(desk, q, kind=''):
    """Recherche dans les données de toutes les pièces : partie, article, montant, date, type."""
    from . import synthese5632
    synthese5632.schema(desk)
    words = [w for w in re.split(r'\s+', str(q or '').strip().lower()) if len(w) >= 2][:5]
    if not words and not kind:
        return []
    where, params = ['method=?'], [synthese5632.METHOD]
    for w in words:
        where.append('lower(digest) LIKE ?')
        params.append('%' + w.replace('%', '') + '%')
    labels = _labels(desk)
    out = []
    for r in desk.db.execute('SELECT matter, path, digest FROM piece_digests5634 WHERE ' + ' AND '.join(where) + ' ORDER BY created DESC LIMIT 400', params):
        try:
            d = json.loads(r['digest'] or '{}')
        except ValueError:
            continue
        if kind and kind.lower() not in str(d.get('nature') or '').lower():
            continue
        hits = []
        for key, lab in (('parties', 'Partie'), ('references', 'Texte cité'), ('montants', 'Montant'), ('faits', 'Fait'), ('demandes', 'Demande')):
            for item in d.get(key) or []:
                text = json.dumps(item, ensure_ascii=False).lower() if not isinstance(item, str) else item.lower()
                if all(w in text for w in words):
                    hits.append('%s : %s' % (lab, _text(item, 'nom', 'reference', 'montant', 'texte', 'fait') or str(item)[:120]))
        out.append({'matter': r['matter'], 'matter_label': labels.get(r['matter'], r['matter']), 'path': r['path'], 'name': r['path'].rsplit('/', 1)[-1],
                    'type': str(d.get('nature') or ''), 'date': str(d.get('date_document') or ''), 'hits': hits[:6]})
        if len(out) >= 100:
            break
    return out


def export_csv(desk, mid):
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=';')
    w.writerow(['Pièce', 'Type', 'Date', 'Parties', 'Dates clés', 'Montants', 'Textes cités', 'Demandes'])
    for r in piece_records(desk, mid):
        w.writerow([r['name'], r['type'], r['date'], ' | '.join(r['parties']), ' | '.join('%s %s' % (x['date'], x['texte']) for x in r['dates']),
                    ' | '.join('%s %s' % (x['montant'], x['objet']) for x in r['montants']), ' | '.join(r['textes']), ' | '.join(r['demandes'])])
    return {'download': {'filename': 'donnees-pieces-%s.csv' % re.sub(r'[^A-Za-z0-9_.-]', '_', mid)[:60], 'mime': 'text/csv', 'text': '﻿' + buf.getvalue()}}


def analyse(desk, mid, owner='cabinet'):
    if mid not in {m['id'] for m in load_matters(desk.c)}:
        raise Stop('dossier_absent')
    job = desk.enqueue('digest_pieces5632', {'matter': mid}, priority=40)
    desk.audit('ged5636_analyse', {'matter': mid, 'owner': owner})
    return {'job': job, 'message': 'Analyse des pièces lancée : les données apparaissent au fil des relevés (quelques minutes par pièce longue).'}


# ------------------------------------------------------------------------------------------------ pièces jointes des courriels
def _safe_name(name):
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', '_', str(name or '')).strip(' .')
    return (name or 'piece-jointe')[:150]


def file_attachments(desk, mail_key, matter='', owner='cabinet', dav=None, mailbox=None, uid='', mail_folder=''):
    """Range les pièces jointes d'un courriel dans le dossier : « PJ courriels/AAAA-MM-JJ - expéditeur/ » ; renvoie les fichiers rangés.

    Le courriel est retrouvé par son relevé (courriel traité par AxiorHub) ou, depuis la boîte de réception, par son numéro."""
    from .desk import report_for
    schema(desk)
    try:
        report = report_for(desk.c, mail_key)
    except Stop:
        if not str(uid).isdigit():
            raise
        report = {'source_uid': str(uid), 'source_mailbox': mail_folder}
    if not matter and re.fullmatch(r'[0-9a-f]{64}', mail_key or ''):
        row = desk.db.execute('SELECT matter FROM work_items WHERE mail_key=?', (mail_key,)).fetchone()
        matter = (row['matter'] if row else '') or str(report.get('matter') or '')
    matters = {m['id']: m for m in load_matters(desk.c)}
    if matter not in matters:
        raise Stop('dossier_du_courriel_a_preciser')
    base = matters[matter]['path']
    from . import mailbox as mbx
    box = mailbox or mbx.Mailbox(desk.c['mail'])
    try:
        mail = box.fetch(report.get('source_mailbox') or desk.c['mail'].get('inbox', 'INBOX'), str(report.get('source_uid') or ''))
    finally:
        if mailbox is None:
            try:
                box.close()
            except Exception:   # noqa: BLE001
                pass
    if not mail_key:
        mail_key = 'uid:%s:%s' % (report.get('source_mailbox') or 'INBOX', report.get('source_uid'))
    stamp = report.get('received_at') or (mail.timestamp.isoformat() if getattr(mail, 'timestamp', None) else datetime.now(timezone.utc).isoformat())
    day = str(stamp)[:10]
    sender = re.sub(r'[^\w .@-]', '', str(report.get('sender') or mail.msg.get('From', '') or 'expediteur').split('<')[-1].rstrip('>'))[:60] or 'expediteur'
    folder = base.rstrip('/') + '/PJ courriels/' + day + ' - ' + _safe_name(sender)
    from .dav import DAV
    client = dav or DAV(desk.c['nextcloud'])
    filed, skipped = [], []
    for part in mail.msg.iter_attachments():
        name = _safe_name(part.get_filename() or '')
        data = part.get_payload(decode=True)
        if not data or not name.lower().endswith(ALLOWED) or len(data) > MAX_ATTACHMENT:
            skipped.append(name)
            continue
        sha = hashlib.sha256(data).hexdigest()
        if desk.db.execute('SELECT 1 FROM attachments_filed5636 WHERE mail_key=? AND sha256=?', (mail_key, sha)).fetchone():
            skipped.append(name)
            continue
        client.ensure_folder(folder, base)
        stem, dot, ext = name.rpartition('.')
        for n in range(1, 30):   # création exclusive : un fichier du même nom n'est jamais remplacé, la copie est numérotée
            target = folder + '/' + (name if n == 1 else ((stem + ' (%d).' % n + ext) if dot else (name + ' (%d)' % n)))
            try:
                client.put_file(target, data, part.get_content_type() or 'application/octet-stream')
                break
            except Stop as exc:
                if str(exc) != 'http_412':
                    raise
        else:
            skipped.append(name)
            continue
        desk.db.execute('INSERT OR IGNORE INTO attachments_filed5636 VALUES(?,?,?,?,?)', (mail_key, sha, matter, target, datetime.now(timezone.utc).isoformat()))
        desk.db.commit()
        filed.append(target)
    desk.audit('ged5636_pj_rangees', {'matter': matter, 'filed': len(filed), 'skipped': len(skipped), 'owner': owner})
    return {'matter': matter, 'filed': filed, 'skipped': skipped,
            'message': ('%d pièce(s) jointe(s) rangée(s) dans « %s ».' % (len(filed), folder)) if filed else 'Aucune nouvelle pièce jointe à ranger.'}


def set_auto(desk, value, owner='cabinet'):
    desk.setting(AUTO, bool(value))
    desk.audit('ged5636_pj_auto', {'enabled': bool(value), 'owner': owner})
    return {'enabled': bool(value), 'message': 'Rangement automatique des pièces jointes ' + ('activé.' if value else 'désactivé.')}


def after_mail(desk, mail_key, matter):
    """Appelé quand un courriel est rattaché à un dossier : rangement automatique (travail de fond) si l'avocat l'a activé."""
    if desk.settings(AUTO, False) and matter and mail_key:
        try:
            desk.enqueue('pj_courriel5636', {'key': mail_key, 'matter': matter}, priority=60)
        except Stop:
            pass


# ------------------------------------------------------------------------------------------------ page
def _matter_select(labels, current, name='dossier'):
    return '<select name="%s" aria-label="Dossier"><option value="">Choisir un dossier…</option>%s</select>' % (name, ''.join(
        '<option value="%s"%s>%s</option>' % (e(k, quote=True), ' selected' if k == current else '', e(v))
        for k, v in sorted(labels.items(), key=lambda kv: kv[1].casefold())))


def _badge(statut):
    return '<span class="g5636-ok">documenté</span>' if statut == 'documente' else '<span class="g5636-check">à vérifier</span>'


def page_html(desk, prefix, args):
    labels = _labels(desk)
    mid, q, kind = str(args.get('dossier') or ''), str(args.get('q') or ''), str(args.get('type') or '')
    html = ('<h1>Données des pièces</h1><p class="ax-muted">Chaque pièce relevée : type, date, parties, dates clés, montants, textes cités et '
            'demandes, avec leur statut (« documenté » : trouvé dans l’extrait cité ; « à vérifier » sinon). Ces données servent à l’assistant, '
            'aux initiatives, aux grilles d’analyse et au secrétariat juridique.</p>'
            '<form method="get" class="g5636-bar">%s<button class="ax-btn" type="submit">Afficher</button></form>') % _matter_select(labels, mid)
    html += ('<section class="ax-card"><h2>Interroger toutes les pièces du cabinet</h2><form method="get" class="g5636-bar"><input type="search" name="q" '
             'value="%s" placeholder="Partie, article 1641, 4 500, 2025-03-12…" aria-label="Rechercher dans les données des pièces"><input name="type" value="%s" '
             'placeholder="Type (assignation, contrat…)" aria-label="Type de pièce"><button class="ax-btn ghost" type="submit">Rechercher</button></form>') % (
        e(q, quote=True), e(kind, quote=True))
    if q or kind:
        found = search_all(desk, q, kind)
        html += '<ul class="g5636-results">%s</ul>' % (''.join(
            '<li><a href="%s"><strong>%s</strong></a> — %s · %s%s%s</li>' % (
                e(prefix + '/pieces-donnees?dossier=' + x['matter'], quote=True), e(x['name']), e(x['matter_label']), e(x['type'] or 'type non relevé'),
                (' · ' + e(x['date'])) if x['date'] else '', ('<br><small>' + e(' ; '.join(x['hits'])) + '</small>') if x['hits'] else '')
            for x in found) or '<li class="ax-muted">Aucune pièce ne correspond.</li>')
    html += '</section>'
    if not mid:
        return html
    records = piece_records(desk, mid)
    html += ('<section class="ax-card"><h2>%s</h2><div class="ax-actions"><form class="m5-form m5-inline" data-api="m5636/pieces/analyser" data-reload="1">'
             '<input type="hidden" name="matter" value="%s"><button class="ax-btn" type="submit">Analyser les pièces du dossier</button></form>'
             '<button type="button" class="ax-btn ghost m5-export" data-api="m5636/pieces/export?dossier=%s">Exporter (tableur)</button></div>') % (
        e(labels.get(mid, mid)), e(mid, quote=True), e(mid, quote=True))
    if not records:
        html += '<p class="ax-muted">Aucune pièce relevée pour l’instant : lancez « Analyser les pièces du dossier ».</p>'
    for r in records:
        html += ('<details class="g5636-piece"><summary><strong>%s</strong> · %s%s · %s</summary><dl>'
                 '<dt>Parties</dt><dd>%s</dd><dt>Dates clés</dt><dd>%s</dd><dt>Montants</dt><dd>%s</dd><dt>Textes cités</dt><dd>%s</dd>'
                 '<dt>Demandes</dt><dd>%s</dd></dl></details>') % (
            e(r['name']), e(r['type'] or 'type non relevé'), (' · ' + e(r['date'])) if r['date'] else '',
            'relevé complet' if r['status'] == 'complet' else 'relevé partiel',
            e(', '.join(r['parties'])) or '—',
            '<br>'.join('%s — %s %s' % (e(x['date']), e(x['texte'][:200]), _badge(x['statut'])) for x in r['dates'][:20]) or '—',
            '<br>'.join('%s %s %s' % (e(x['montant']), e(x['objet'][:120]), _badge(x['statut'])) for x in r['montants'][:20]) or '—',
            e(', '.join(r['textes'][:30])) or '—', '<br>'.join(e(x[:200]) for x in r['demandes'][:15]) or '—')
    dl = deadlines(desk, mid)
    html += '<h3>Échéances détectées</h3><ul>%s</ul></section>' % (''.join(
        '<li>%s — %s <small>(%s)</small></li>' % (e(x.get('due') or 'date à confirmer'), e(x.get('label') or x.get('rule_label') or x.get('rule_id', '')),
                                                   e(x.get('status', ''))) for x in dl[:30]) or '<li class="ax-muted">Aucune échéance détectée.</li>')
    auto = bool(desk.settings(AUTO, False))
    html += ('<section class="ax-card"><h2>Pièces jointes des courriels</h2><p class="ax-muted">Les pièces jointes d’un courriel rattaché à un dossier '
             'peuvent être rangées dans « PJ courriels » du dossier (bouton « Ranger les pièces jointes » d’un courriel, ou initiative). Sans doublon, '
             'sans écraser un fichier.</p><form class="m5-form m5-inline" data-api="m5636/pieces/pj-auto" data-reload="1"><label class="e5636-switch">'
             '<input type="checkbox" name="enabled"%s> Ranger automatiquement les pièces jointes des courriels rattachés à un dossier</label>'
             '<button class="ax-btn ghost" type="submit">Enregistrer</button></form></section>') % (' checked' if auto else '')
    return html


def api_get(desk, route, args):
    if route == 'pieces':
        return {'pieces': piece_records(desk, str(args.get('dossier') or ''))}
    if route == 'pieces/recherche':
        return {'results': search_all(desk, args.get('q', ''), args.get('type', ''))}
    if route == 'pieces/export':
        return export_csv(desk, str(args.get('dossier') or ''))
    return None


def api_post(desk, route, data, owner):
    if route == 'pieces/analyser':
        return analyse(desk, str(data.get('matter') or ''), owner)
    if route == 'pieces/ranger-pj':
        return file_attachments(desk, str(data.get('key') or ''), str(data.get('matter') or ''), owner, uid=str(data.get('uid') or ''),
                                mail_folder=str(data.get('folder') or ''))
    if route == 'pieces/pj-auto':
        return set_auto(desk, bool(data.get('enabled')), owner)
    return None
