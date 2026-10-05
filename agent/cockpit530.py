"""Poste de pilotage « Aujourd'hui » (AxiorHub 5.3.0).

Un seul écran centré sur l'agent :
- en haut, la zone d'instruction (texte, dictée, pièces jointes) : l'agent devine le dossier, l'avocat peut le corriger ; une demande de
  document part vers la rédaction (docrequest520), une question vers l'assistant ; l'agent répond dans le fil et poursuit son travail
  automatique ;
- trois colonnes : « À relire » (brouillons et documents, ouverts dans un panneau latéral, sans changer de page), « Ce que fait l'agent »
  (en cours, fait, bloqué avec le motif et l'action), « Ma journée » (agenda, Tâches Nextcloud, tableau Deck) ;
- les routines (briefing, tri, bilan, documents à préparer) en onglets, et l'apprentissage du style.

Lecture seule par défaut : seules les actions explicites écrivent (demande, relecture, tâche cochée, réglages Deck/Tâches).
Toutes les valeurs affichées sont échappées ; le navigateur reçoit des fragments HTML construits ici.
"""
from datetime import date, datetime, time as dtime, timedelta, timezone
from html import escape as e
import json
from pathlib import PurePosixPath
import re
import time
from urllib.parse import urlencode
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .common import Stop, clean_path, digest, fold, load_matters, matter_display, under

DOC_VERBS = ('prepare', 'redige', 'rediger', 'ecris', 'ecrire', 'etablis', 'etablir', 'fais', 'faire', 'drafte', 'propose un projet', 'genere', 'produis')
TYPE_LABELS = {'conclusions': 'Conclusions', 'assignation': 'Acte de procédure', 'courrier': 'Courrier', 'courriel': 'Projet de courriel',
               'mise_en_demeure': 'Mise en demeure', 'contrat': 'Contrat', 'note': 'Note', 'compte_rendu': 'Compte rendu'}
TYPE_CLASS = {'Courriel': 'mail', 'Conclusions': 'proc', 'Acte de procédure': 'proc', 'Courrier': 'corr', 'Projet de courriel': 'corr',
              'Mise en demeure': 'corr', 'Compte rendu': 'corr', 'Contrat': 'conseil', 'Note': 'conseil'}
NOISE = {'live_calendar430', 'live_documents430', 'health', 'snapshot_metrics420', 'automation_setting', 'deck530_sync', 'verify_deliverable420',
         'refresh_operational_memory', 'sync_legal_memory', 'extract_facts460', 'build_daily_dashboard', 'memory_all', 'index_all', 'monitor_all',
         'orchestrator_mail_sweep', 'autonomy_mail_sweep', 'sync', 'discover'}
MAIL_KINDS = {'run', 'live_mail430'}


def ensure_schema(desk):
    desk.db.executescript('''
    CREATE TABLE IF NOT EXISTS cockpit530_messages(id INTEGER PRIMARY KEY, role TEXT NOT NULL, text TEXT NOT NULL, matter TEXT NOT NULL,
      ref TEXT NOT NULL, created TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS cockpit530_reviewed(item TEXT PRIMARY KEY, decision TEXT NOT NULL, title TEXT NOT NULL, at TEXT NOT NULL);
    ''')
    desk.db.commit()


def tz(desk):
    try:
        return ZoneInfo(desk.c.get('calendar', {}).get('timezone', 'Europe/Paris'))
    except ZoneInfoNotFoundError:
        return timezone.utc


def _labels(desk):
    try:
        return {m['id']: matter_display(m) for m in load_matters(desk.c)}
    except Stop:
        return {}


def _matters(desk):
    try:
        return {m['id']: m for m in load_matters(desk.c)}
    except Stop:
        return {}


def _reason(code):
    from .web520 import _reason as r
    return r(code)


def _local(desk, stamp, fmt='%H:%M'):
    try:
        value = datetime.fromisoformat(str(stamp))
        if not value.tzinfo:
            value = value.replace(tzinfo=timezone.utc)
        local = value.astimezone(tz(desk))
        today = datetime.now(tz(desk)).date()
        if local.date() == today:
            return local.strftime(fmt)
        if local.date() == today - timedelta(days=1):
            return 'hier ' + local.strftime(fmt)
        return local.strftime('%d/%m ') + local.strftime(fmt)
    except (TypeError, ValueError):
        return ''


def _ago(seconds):
    seconds = max(0, int(seconds))
    if seconds < 90:
        return 'à l’instant'
    if seconds < 5400:
        return 'depuis %d min' % (seconds // 60)
    return 'depuis %d h' % (seconds // 3600)


# ======================================================================================== dossier deviné
def guess(desk, text):
    from .docrequest520 import find_matter
    text = str(text or '')[:4000]
    if len(text.strip()) < 3:
        return {'matter': '', 'label': '', 'how': 'en attente', 'candidates': []}
    from . import maildigest562
    if maildigest562.detect(text, datetime.now(tz(desk)).date()):
        return {'matter': '', 'label': '', 'how': 'question sur les courriels reçus : tout le cabinet', 'candidates': []}
    matter, candidates = find_matter(desk, text)
    if matter:
        return {'matter': matter['id'], 'label': matter_display(matter), 'how': 'deviné par l’agent', 'candidates': []}
    return {'matter': '', 'label': '', 'how': 'à préciser' if candidates else 'en attente',
            'candidates': [{'id': m['id'], 'label': matter_display(m)} for m in candidates[:6]]}


def is_document_request(text):
    from .docrequest520 import guess_kind
    f = ' ' + re.sub(r'[^a-z0-9]+', ' ', fold(text)) + ' '
    verb = any(' %s ' % v in f or f.lstrip().startswith(v + ' ') for v in DOC_VERBS)
    return verb and guess_kind(text) != 'auto'


# ======================================================================================== conversation
def ask(desk, data):
    ensure_schema(desk)
    text = re.sub(r'[ \t]+', ' ', str(data.get('text') or '')).strip()
    if not 3 <= len(text) <= 4000:
        raise Stop('instruction_vide_ou_trop_longue')
    files = [str(x) for x in (data.get('attachments') or []) if x][:3] if isinstance(data.get('attachments'), list) else []
    chosen = str(data.get('matter') or '')
    matters = _matters(desk)
    if chosen and chosen not in matters:
        raise Stop('dossier_absent')
    if str(data.get('mode') or '') != 'document' and not files:
        from . import maildigest562
        digest = maildigest562.answer(desk, text, matter=chosen)
        if digest is not None:
            now = desk.now()
            desk.db.execute('INSERT INTO cockpit530_messages(role,text,matter,ref,created) VALUES(?,?,?,?,?)', ('user', text, chosen, '', now))
            desk.db.execute('INSERT INTO cockpit530_messages(role,text,matter,ref,created) VALUES(?,?,?,?,?)', ('agent', digest, chosen, '', now))
            desk.db.commit()
            desk.audit('cockpit530_instruction', {'matter': chosen, 'kind': 'resume_courriels', 'attachments': 0})
            return {'message': 'Résumé des courriels prêt.', 'matter': chosen, 'ref': ''}
    g = guess(desk, text)
    mid = chosen or g['matter']
    label = matter_display(matters[mid]) if mid else ''
    if str(data.get('mode') or '') == 'document' or is_document_request(text):
        from . import docrequest520 as dr
        out = dr.submit(desk, text, mid, 'auto', '', files)
        ref = 'docreq:' + out['request']
        reply = ('Compris. Je travaille dans le dossier « %s » : lecture des pièces, des courriels et de l’agenda du dossier%s. '
                 'Le projet arrivera dans « À relire ».' % (label, (' et de %d pièce(s) jointe(s)' % len(files)) if files else '')) if mid else \
            'Compris. Je cherche d’abord le dossier concerné ; s’il y a un doute, je vous demanderai de le préciser.'
    else:
        from .integration import submit_question
        out = submit_question(desk, text, mid, attachment_ids=files)
        ref = 'ask:%s:%s' % (out['thread_id'], out['message_id'])
        reply = 'Je regarde%s et je vous réponds ici.' % ((' dans le dossier « %s »' % label) if mid else ' dans l’ensemble du cabinet')
    now = desk.now()
    desk.db.execute('INSERT INTO cockpit530_messages(role,text,matter,ref,created) VALUES(?,?,?,?,?)', ('user', text, mid, '', now))
    desk.db.execute('INSERT INTO cockpit530_messages(role,text,matter,ref,created) VALUES(?,?,?,?,?)', ('agent', reply, mid, ref, now))
    desk.db.commit()
    desk.audit('cockpit530_instruction', {'matter': mid, 'kind': ref.split(':', 1)[0], 'attachments': len(files)})
    return {'message': 'Instruction transmise à l’agent.', 'matter': mid, 'ref': ref}


def _follow_up(desk, prefix, ref):
    """Suite donnée par l'agent : (html, en attente ?)."""
    kind, _, rest = ref.partition(':')
    if kind == 'docreq':
        row = desk.db.execute('SELECT * FROM docreq520 WHERE id=?', (rest,)).fetchone()
        if not row:
            return '', False
        res = json.loads(row['result'] or '{}')
        if row['status'] == 'cree':
            name = PurePosixPath(row['path']).name
            # 5.6.5 : où le document a été enregistré (dossier › sous-dossier) et modification directe dans Nextcloud.
            root = (_matters(desk).get(row['matter']) or {}).get('path', '')
            rel = row['path'][len(root):].lstrip('/') if root and row['path'].startswith(root) else row['path']
            folder = str(PurePosixPath(rel).parent)
            where = _labels(desk).get(row['matter'], '') + ((' › ' + folder.replace('/', ' › ')) if folder not in ('', '.') else '')
            return ('Document « %s » prêt : il vous attend dans « À relire ». Enregistré dans %s. <button type="button" class="ax-btn ghost c530-small" data-open-path="%s" '
                    'data-open-matter="%s">Modifier le document</button> <a href="%s">Éditeur AxiorHub</a>' % (
                e(name), e(where or 'le dossier'), e(row['path'], quote=True), e(row['matter'], quote=True),
                e(prefix + '/documents/edit?' + urlencode({'path': row['path'], 'matter': row['matter']})))), False
        if row['status'] == 'dossier_a_choisir':
            cands = ', '.join(c['label'] for c in res.get('candidates', [])[:4])
            return e('Je n’ai pas trouvé le dossier avec certitude%s. Précisez-le avec « Corriger » puis renvoyez la demande.' % (
                (' (peut-être : %s)' % cands) if cands else '')), False
        if row['status'] == 'echec':
            return e('Je n’ai pas pu préparer ce document : %s' % _reason(res.get('error', 'action_interrompue'))), False
        if row['status'] == 'annule':
            return e('Demande arrêtée à votre demande.'), False
        return e('Rédaction en cours…' if row['status'] == 'en_cours' else 'En file d’attente…'), True
    if kind == 'ask':
        thread, _, mid = rest.partition(':')
        try:
            mid = int(mid)
        except ValueError:
            return '', False
        answer = desk.db.execute("SELECT content,status FROM assistant_messages WHERE thread_id=? AND id>? AND role='assistant' ORDER BY id LIMIT 1",
                                 (thread, mid)).fetchone()
        if answer:
            text = answer['content'] or ''
            return e(text[:3000] + ('…' if len(text) > 3000 else '')), False
        q = desk.db.execute('SELECT status FROM assistant_messages WHERE id=?', (mid,)).fetchone()
        if q and q['status'] == 'cancelled':
            return e('Question arrêtée à votre demande.'), False
        if q and q['status'] == 'error':
            return e('Je n’ai pas pu répondre (voir « Pourquoi rien n’est produit ? »).'), False
        return e('Je cherche…'), True
    return '', False


def thread_html(desk, prefix):
    ensure_schema(desk)
    rows = list(reversed(desk.db.execute('SELECT * FROM cockpit530_messages ORDER BY id DESC LIMIT 8').fetchall()))
    out, waiting = [], False
    if not rows:
        greeting = greeting_text(desk)
        out.append('<div class="c530-msg agent"><p>%s</p></div>' % e(greeting))
    for r in rows:
        if r['role'] == 'user':
            out.append('<div class="c530-msg me"><p>%s</p></div>' % e(r['text']))
            continue
        extra, pending = _follow_up(desk, prefix, r['ref']) if r['ref'] else ('', False)
        waiting = waiting or pending
        stop = ('<button type="button" class="ax-btn ghost c530-small c561-stop" data-stop="%s">Arrêter</button>' % e(r['ref'], quote=True)) if pending else ''
        out.append('<div class="c530-msg agent"><p>%s</p>%s%s</div>' % (e(r['text']), ('<p class="c530-follow%s">%s</p>' % (' pending' if pending else '', extra)) if extra else '', stop))
    tools = ('<div class="c561-thread-tools"><button type="button" class="ax-btn ghost c530-small" data-clear="1" title="Efface les messages affichés ; '
             'les travaux et documents ne sont pas touchés">Effacer la discussion</button></div>') if rows else ''
    return '%s<div class="c530-thread" data-waiting="%s" aria-live="polite">%s</div>' % (tools, '1' if waiting else '0', ''.join(out))


def job_of(desk, ref):
    """Travail de la file correspondant à une demande du fil (« docreq:… » ou « ask:… »), ou 0."""
    kind, _, rest = str(ref or '').partition(':')
    if kind == 'docreq':
        row = desk.db.execute('SELECT job_id FROM docreq520 WHERE id=?', (rest,)).fetchone() if _table(desk, 'docreq520') else None
        return int(row['job_id'] or 0) if row else 0
    if kind == 'ask':
        try:
            mid = int(rest.partition(':')[2])
        except ValueError:
            return 0
        row = desk.db.execute('SELECT job_id FROM assistant_messages WHERE id=?', (mid,)).fetchone()
        return int(row['job_id'] or 0) if row else 0
    return 0


def stop(desk, data):
    """Arrête la tâche demandée dans le fil : annulée si elle attend, arrêt demandé si elle a commencé."""
    ref = str(data.get('ref') or '')
    job = job_of(desk, ref)
    if not job:
        raise Stop('travail_invalide')
    try:
        out = desk.cancel_job(job)
    except Stop as ex:
        if str(ex) == 'operation_deja_terminee':
            return {'message': 'Cette tâche est déjà terminée.'}
        raise
    kind, _, rest = ref.partition(':')
    if out['status'] == 'cancelled':
        if kind == 'docreq':
            desk.db.execute("UPDATE docreq520 SET status='annule', updated=? WHERE id=?", (desk.now(), rest))
        else:
            desk.db.execute("UPDATE assistant_messages SET status='cancelled', updated=? WHERE id=?", (desk.now(), int(rest.partition(':')[2])))
        desk.db.commit()
        return {'message': 'Tâche arrêtée avant son démarrage.'}
    return {'message': 'Arrêt demandé : le travail en cours s’arrête sans être relancé (un document déjà déposé reste conservé).'}


def clear(desk):
    """Efface la discussion affichée (les travaux, documents et brouillons ne sont pas touchés)."""
    ensure_schema(desk)
    n = desk.db.execute('DELETE FROM cockpit530_messages').rowcount
    desk.db.commit()
    desk.audit('cockpit530_discussion_effacee', {'messages': n})
    return {'message': 'Discussion effacée.'}


def cancel(desk, data):
    """Annule un travail depuis son détail (« Ce que fait l'agent »)."""
    try:
        jid = int(data.get('job'))
    except (TypeError, ValueError):
        raise Stop('travail_invalide') from None
    out = desk.cancel_job(jid)
    return {'message': 'Travail annulé.' if out['status'] == 'cancelled' else 'Arrêt demandé : le travail en cours s’arrête sans être relancé.'}


def greeting_text(desk):
    since = (datetime.now(timezone.utc) - timedelta(hours=16)).isoformat()
    try:
        from .state import State
        examined = State(desk.c['state_dir']).db.execute('SELECT COUNT(*) FROM messages WHERE updated>=?', (since,)).fetchone()[0]
    except Exception:
        examined = 0
    items = review_items(desk, cached_only=True)
    mails = sum(1 for x in items if x['type'] == 'Courriel')
    docs = len(items) - mails
    return ('Bonjour Maître. Depuis hier soir : %d courriel(s) analysé(s) ; %d brouillon(s) et %d document(s) à relire. '
            'Donnez-moi une instruction, ou laissez-moi continuer seul.' % (examined, mails, docs))


# ======================================================================================== à relire
def _drafts(desk, refresh=True):
    cache = desk.settings('cockpit530:drafts', None) or {}
    if refresh and time.time() - float(cache.get('at', 0)) > 120:
        try:
            from . import drafts440
            listing = drafts440.list_drafts(desk.c['mail'], 40)
            cache = {'at': time.time(), 'ok': True, 'validity': listing['uidvalidity'],
                     'items': [{k: x.get(k, '') for k in ('uid', 'subject', 'to', 'date', 'agent_key', 'message_id', 'in_reply_to')}
                               for x in listing['items'] if x.get('agent')]}
        except Exception:
            cache = {**cache, 'at': time.time(), 'ok': False}
        desk.setting('cockpit530:drafts', cache)
    return cache


def _draft_matter(desk, d):
    """Dossier d'un brouillon de l'agent : clé du courriel d'origine (état local, lien courriel-dossier), sinon courriel auquel il répond."""
    keys = []
    if d.get('agent_key'):
        keys.append(str(d['agent_key']).strip())
    mids = [m for m in (d.get('message_id'), d.get('in_reply_to')) if m]
    try:
        from .state import State
        st = State(desk.c['state_dir'])
        for m in mids:
            for form in {m.strip('<>'), '<' + m.strip('<>') + '>'}:
                row = st.db.execute('SELECT key FROM messages WHERE draft_mid=? OR mid=? LIMIT 1', (form, form)).fetchone()
                if row:
                    keys.append(row[0])
    except Exception:
        pass
    for key in keys:
        for sql in ('SELECT matter FROM work_items WHERE mail_key=?', "SELECT matter FROM portfolio_mail_links WHERE mail_key=? AND matter<>'' LIMIT 1"):
            try:
                row = desk.db.execute(sql, (key,)).fetchone()
            except Exception:
                row = None
            if row and row[0]:
                return row[0]
    for m in mids:
        for form in {m.strip('<>'), '<' + m.strip('<>') + '>'}:
            try:
                row = desk.db.execute("SELECT matter FROM portfolio_mail_links WHERE message_id=? AND matter<>'' LIMIT 1", (form,)).fetchone()
            except Exception:
                row = None
            if row:
                return row[0]
    return ''


def review_items(desk, cached_only=False):
    ensure_schema(desk)
    labels, matters = _labels(desk), _matters(desk)
    done = {r[0] for r in desk.db.execute('SELECT item FROM cockpit530_reviewed')}
    out = []
    cache = _drafts(desk, refresh=not cached_only)
    for d in cache.get('items', []):
        item = 'mail:%s:%s' % (cache.get('validity', ''), d['uid'])
        if item in done:
            continue
        mid = _draft_matter(desk, d)
        out.append({'id': item, 'type': 'Courriel', 'title': d['subject'] or '(sans objet)', 'matter': mid, 'matter_label': labels.get(mid, ''),
                    'when': d.get('date', ''), 'dest': 'Brouillons (messagerie) — à %s' % (d.get('to') or 'destinataire')})
    seen = set()
    since = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    try:
        for r in desk.db.execute("SELECT * FROM docreq520 WHERE status='cree' AND updated>=? ORDER BY updated DESC LIMIT 40", (since,)):
            item = 'doc:' + digest(r['path'])[:24]
            seen.add(r['path'])
            if item in done:
                continue
            res = json.loads(r['result'] or '{}')
            root = matters.get(r['matter'], {}).get('path', '')
            out.append({'id': item, 'type': TYPE_LABELS.get(r['kind'], 'Document'), 'title': res.get('title') or PurePosixPath(r['path']).stem,
                        'matter': r['matter'], 'matter_label': labels.get(r['matter'], ''), 'when': r['updated'], 'path': r['path'], 'request': r['id'],
                        'dest': r['path'][len(root):].lstrip('/') if root and r['path'].startswith(root) else r['path']})
    except Exception:
        pass
    try:
        since14 = (datetime.now(timezone.utc) - timedelta(days=14)).isoformat()
        for r in desk.db.execute("SELECT * FROM production_deliverables_v420 WHERE status IN ('verified','verifying','prepared') AND deliverable_kind<>'mail_draft' "
                                 "AND updated>=? ORDER BY updated DESC LIMIT 40", (since14,)):
            files = json.loads(r['verification'] or '{}').get('files', [])
            for f in files[:3]:
                path = str(f.get('path', ''))
                if not path or path in seen or not path.lower().endswith(('.docx', '.odt', '.pdf', '.md', '.txt')):
                    continue
                seen.add(path)
                item = 'doc:' + digest(path)[:24]
                if item in done:
                    continue
                root = matters.get(r['matter'], {}).get('path', '')
                up = fold(path)
                kind = 'Acte de procédure' if '/procedure/' in up else ('Courrier' if '/correspondances/' in up else ('Contrat' if '/projets/' in up else 'Document'))
                out.append({'id': item, 'type': kind, 'title': PurePosixPath(path).stem, 'matter': r['matter'], 'matter_label': labels.get(r['matter'], ''),
                            'when': r['updated'], 'path': path, 'dest': path[len(root):].lstrip('/') if root and path.startswith(root) else path})
    except Exception:
        pass
    out.sort(key=lambda x: str(x['when']), reverse=True)
    return out


def review_html(desk, prefix):
    items = review_items(desk)
    note = '' if desk.settings('cockpit530:drafts', {}).get('ok', True) else '<p class="c530-note">Brouillons de la messagerie non lus pour l’instant (connexion).</p>'
    rows = ''.join(
        '<li><button type="button" class="c530-item" data-item="%s"><span class="c530-row"><span class="c530-type %s">%s</span><span class="c530-when">%s</span></span>'
        '<strong>%s</strong><span class="c530-sub">%s</span><span class="c530-dest">→ %s</span></button></li>' % (
            e(x['id'], quote=True), TYPE_CLASS.get(x['type'], 'doc'), e(x['type']), e(_local(desk, x['when'])), e(x['title']),
            e(x['matter_label'] or 'dossier non identifié'), e(x['dest'])) for x in items[:25])
    return ('<div class="c530-head"><h2 id="c530-t-review">À relire <span class="c530-count">%d</span></h2><span class="c530-hint">un clic pour ouvrir</span></div>%s%s') % (
        len(items), note, ('<ul class="c530-list">%s</ul>' % rows) if rows else '<p class="c530-empty">Tout est relu. L’agent vous préviendra ici du prochain projet.</p>')


def _find(desk, item):
    for x in review_items(desk, cached_only=True):
        if x['id'] == item:
            return x
    raise Stop('element_a_relire_absent')


def item_html(desk, prefix, item):
    x = _find(desk, item)
    sources, actions, body, meta = [], '', '', ''
    if x['type'] == 'Courriel':
        from . import drafts440
        validity, uid = item.split(':')[1:3]
        d = drafts440.get_draft(desk.c['mail'], uid, validity)
        body = d['body']
        meta = '<p class="c530-meta">À : %s</p>' % e(d['to'])
        src = d.get('source') or {}
        if src:
            sources.append('Courriel de %s : « %s »' % (src['sender'], src['subject']))
            # 5.6.5 : le courriel d'origine est visible ici et s'ouvre dans la messagerie.
            meta += ('<details class="c565-origin" open><summary>Courriel d’origine : %s — %s</summary><p class="c530-meta">« %s »</p>'
                     '<pre class="c565-excerpt">%s</pre>%s</details>') % (
                e(src.get('sender', '')), e(_local(desk, src.get('date', ''), '%d/%m %H:%M')), e(src.get('subject', '')),
                e(str(src.get('text', ''))[:1500] + ('…' if len(str(src.get('text', ''))) > 1500 else '')),
                _mail_link(desk, src.get('mailbox', ''), src.get('uid', ''), 'show', 'Ouvrir le courriel d’origine dans la messagerie'))
        revise = bool(src.get('key'))
        actions = ('<a class="ax-btn ghost" href="%s">Ouvrir dans Courriels à relire</a>' % e(prefix + '/courriels?' + urlencode({'uid': d.get('uid', uid)})) +
                   _mail_link(desk, desk.c['mail'].get('drafts', 'Drafts'), d.get('uid', uid), 'edit', 'Brouillon dans la messagerie') +
                   '<button type="button" class="ax-btn" data-act="relu">Marquer comme relu</button>')
        hidden = '<input type="hidden" name="key" value="%s">' % e(d['source']['key'], quote=True) if revise else ''
    else:
        from .document_projects import _dav
        from .documents import extract
        client = _dav(desk)
        raw = client.download({**client.stat(x['path']), 'path': x['path']})
        text = extract(raw, PurePosixPath(x['path']).name, {**desk.c.get('documents', {}), 'max_document_chars': 60000})
        body = text if isinstance(text, str) else text.get('text', '')
        if x.get('request'):
            row = desk.db.execute('SELECT result FROM docreq520 WHERE id=?', (x['request'],)).fetchone()
            res = json.loads(row['result'] or '{}') if row else {}
            sources += ['Sources citées : ' + ', '.join(res.get('sources', [])[:12])] if res.get('sources') else []
            sources += ['À compléter : ' + ' ; '.join(res.get('to_complete', [])[:8])] if res.get('to_complete') else []
            sources += res.get('notes', [])[:3]
        url = ''
        try:
            url = client.file_web_url(x['path'])
        except Exception:
            pass
        # 5.6.5 : emplacement exact du document et modification directe (Nextcloud / OnlyOffice en premier).
        folder = str(PurePosixPath(x['dest']).parent) if '/' in x['dest'] else ''
        meta = ('<p class="c565-where"><strong>Emplacement :</strong> %s%s › <strong>%s</strong></p>' % (
            e(x['matter_label'] or 'dossier non identifié'), (' › ' + e(folder.replace('/', ' › '))) if folder and folder != '.' else '',
            e(PurePosixPath(x['path']).name)))
        actions = (('<a class="ax-btn" target="_blank" rel="noopener noreferrer" href="%s">Modifier le document</a>' % e(url, quote=True)) if url.startswith('https://') else
                   '<button type="button" class="ax-btn" data-open-path="%s" data-open-matter="%s">Modifier le document</button>' % (
                       e(x['path'], quote=True), e(x['matter'], quote=True))) + (
                   '<a class="ax-btn ghost" href="%s">Éditeur AxiorHub</a>' % e(prefix + '/documents/edit?' + urlencode({'path': x['path'], 'matter': x['matter']}))) + (
                   '<button type="button" class="ax-btn ghost" data-act="valide">Valider le projet</button>')
        revise, hidden = True, ''
    revise_html = ('<div class="c530-revise">%s<label class="c530-sr" for="c530-rev">Faire modifier par l’IA</label><input id="c530-rev" name="instruction" maxlength="2000" '
                   'placeholder="Faire modifier par l’IA : ex. ton plus ferme, ajouter la date d’audience"><button type="button" class="ax-btn ghost" data-act="revise">'
                   'Nouvelle version</button></div>' % hidden) if revise else ''
    return ('<div class="c530-drawer-head"><span class="c530-type %s">%s</span><h2 id="c530-drawer-title">%s</h2><p class="c530-sub">%s</p><p class="c530-meta">Enregistré : %s</p>%s</div>'
            '<div class="c530-drawer-body"><div class="c530-doc">%s</div>%s</div>'
            '<div class="c530-drawer-foot" data-item="%s">%s<div class="c530-actions">%s<button type="button" class="ax-btn ghost" data-act="ecarte">Ignorer</button></div></div>') % (
        TYPE_CLASS.get(x['type'], 'doc'), e(x['type']), e(x['title']), e(x['matter_label'] or 'dossier non identifié'), e(x['dest']), meta,
        e(body[:15000] + ('\n…' if len(body) > 15000 else '')),
        ('<h3>Sources</h3><ul>%s</ul>' % ''.join('<li>%s</li>' % e(s) for s in sources)) if sources else '',
        e(item, quote=True), revise_html, actions)


def _mail_link(desk, mailbox, uid, action, label):
    """Lien Roundcube vers un message précis (lecture du courriel d'origine ou édition du brouillon) ; rien si le webmail n'est pas configuré."""
    from urllib.parse import quote
    try:
        from .workstation import external_links
        base = external_links(desk).get('roundcube') or ''
    except Exception:
        base = ''
    uid = re.sub(r'\D', '', str(uid or ''))[:12]
    if not base.startswith('https://') or not uid or not mailbox:
        return ''
    url = '%s?_task=mail&_mbox=%s&_uid=%s&_action=%s' % (base.split('?', 1)[0], quote(str(mailbox), safe=''), uid, action)
    return '<a class="ax-btn ghost" target="_blank" rel="noopener noreferrer" href="%s">%s</a>' % (e(url, quote=True), e(label))


def review(desk, data):
    item, decision = str(data.get('item') or ''), str(data.get('decision') or '')
    if decision not in ('valide', 'relu', 'ecarte'):
        raise Stop('decision_invalide')
    x = _find(desk, item)
    desk.db.execute('INSERT OR REPLACE INTO cockpit530_reviewed VALUES(?,?,?,?)', (item, decision, ('%s — %s' % (x['title'], x['matter_label']))[:250], desk.now()))
    desk.db.commit()
    try:
        from .learning410 import record_review
        record_review(desk, item, x['matter'], 'mail_drafting' if x['type'] == 'Courriel' else 'document_drafting',
                      {'valide': 'accepted', 'relu': 'accepted', 'ecarte': 'rejected'}[decision])
    except Exception:
        pass
    desk.audit('cockpit530_relecture', {'item': digest(item)[:16], 'decision': decision})
    msg = {'valide': 'Projet validé. Il passe dans « Fait ».', 'relu': 'Brouillon marqué comme relu ; il reste dans votre dossier Brouillons.',
           'ecarte': 'Projet écarté. L’agent en tiendra compte. (Le fichier ou le brouillon n’est pas supprimé.)'}[decision]
    return {'message': msg}


def revise(desk, data):
    item, instruction = str(data.get('item') or ''), re.sub(r'\s+', ' ', str(data.get('instruction') or '')).strip()
    if not 3 <= len(instruction) <= 2000:
        raise Stop('instruction_vide_ou_trop_longue')
    x = _find(desk, item)
    if x['type'] == 'Courriel':
        key = str(data.get('key') or '')
        if not re.fullmatch(r'[0-9a-f]{64}', key):
            raise Stop('courriel_source_introuvable')
        job = desk.enqueue('prepare_reply', {'key': key, 'instruction': instruction})
        return {'message': 'Nouveau brouillon demandé (travail n° %d). L’ancien reste dans Brouillons.' % job}
    from . import docrequest520 as dr
    if x.get('request'):
        out = dr.submit(desk, instruction, '', 'auto', x['request'])
    else:
        out = dr.submit(desk, instruction, x['matter'], 'auto', '', None, x['path'])
    ensure_schema(desk)
    desk.db.execute('INSERT INTO cockpit530_messages(role,text,matter,ref,created) VALUES(?,?,?,?,?)',
                    ('agent', 'Nouvelle version de « %s » demandée : %s' % (x['title'], instruction), x['matter'], 'docreq:' + out['request'], desk.now()))
    desk.db.commit()
    return {'message': 'Nouvelle version demandée : elle arrivera dans « À relire », à côté de l’original.'}


# ======================================================================================== ce que fait l'agent
def feed(desk):
    from .web import JOB_LABELS
    from . import queue521
    labels = _labels(desk)
    label = lambda k: 'Analyse des nouveaux courriels' if k in MAIL_KINDS else JOB_LABELS.get(k, k)
    out = {'en_cours': [], 'fait': [], 'bloque': []}

    def matter_of(raw):
        try:
            args = json.loads(raw or '{}')
            if not isinstance(args, dict):
                return ''
            what = _what(desk, args)
            where = labels.get(str(args.get('matter', '')), '')
            return ' — '.join(x for x in (where, what) if x)
        except ValueError:
            return ''
    for r in desk.db.execute("SELECT id,kind,args,status,created,started FROM jobs WHERE status IN ('running','pending','cancel_requested') "
                             "ORDER BY CASE status WHEN 'running' THEN 0 ELSE 1 END, priority, id LIMIT 40"):
        if r['kind'] in NOISE:
            continue
        last = desk.db.execute('SELECT message FROM live_events_v430 WHERE job_id=? ORDER BY id DESC LIMIT 1', (r['id'],)).fetchone() if r['status'] == 'running' else None
        out['en_cours'].append({'job': r['id'], 'text': label(r['kind']), 'matter': matter_of(r['args']), 'running': r['status'] == 'running',
                                'when': _ago(queue521._age_seconds(r['started'] or r['created'])) if r['status'] == 'running' else 'en file',
                                'detail': (last['message'] if last else '')})
        if len(out['en_cours']) >= 8:
            break
    since = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    for r in desk.db.execute("SELECT id,kind,args,finished,result FROM jobs WHERE status='done' AND finished>=? ORDER BY id DESC LIMIT 60", (since,)):
        if r['kind'] in NOISE or len(out['fait']) >= 10:
            continue
        msg = desk.db.execute('SELECT business_message FROM production_deliverables_v420 WHERE job_id=? ORDER BY updated DESC LIMIT 1', (r['id'],)).fetchone() \
            if _table(desk, 'production_deliverables_v420') else None
        detail = ''
        if r['kind'] in MAIL_KINDS:
            try:
                n = int(json.loads(r['result'] or '{}').get('examined', 0))
            except (ValueError, TypeError):
                n = 0
            if not n:
                continue
            detail = '%d courriel(s) examiné(s)' % n
        out['fait'].append({'job': r['id'], 'text': msg['business_message'] if msg else label(r['kind']), 'matter': matter_of(r['args']), 'when': _local(desk, r['finished']),
                            'detail': detail})
    for r in desk.db.execute("SELECT id,kind,args,priority,finished,result FROM jobs WHERE status='error' AND finished>=? ORDER BY id DESC LIMIT 40", (since,)):
        try:
            code = json.loads(r['result'] or '{}').get('erreur', 'action_interrompue')
        except ValueError:
            code = 'action_interrompue'
        if r['kind'] in NOISE or (code == queue521.INTERRUPTED and queue521.automatic(r['kind'], r['priority'])) or len(out['bloque']) >= 8:
            continue
        out['bloque'].append({'job': r['id'], 'text': label(r['kind']), 'matter': matter_of(r['args']), 'when': _local(desk, r['finished']), 'detail': _reason(code),
                              'retry': r['id'] if r['kind'] not in ('automation_setting',) else 0})
    try:
        from .state import State
        n = State(desk.c['state_dir']).db.execute("SELECT COUNT(*) FROM messages WHERE status='review' AND updated>=? AND "
                                                  "(reason LIKE '%correspondant%' OR reason LIKE '%dossier%')", (since,)).fetchone()[0]
        if n:
            out['bloque'].insert(0, {'text': '%d courriel(s) sans brouillon : correspondant ou dossier à confirmer' % n, 'matter': '', 'when': '24 h',
                                     'detail': 'Confirmez l’expéditeur et son dossier : les messages suivants seront rédigés.', 'link': '/associations',
                                     'link_label': 'Rattacher'})
    except Exception:
        pass
    try:
        from . import conflicts500
        for p in conflicts500.pending(desk)[:3]:
            if conflicts500.matter_gate_state(desk, p['matter'])['gated']:
                out['bloque'].append({'text': 'Production suspendue : conflits d’intérêts à examiner', 'matter': p['label'], 'when': '',
                                      'detail': 'Aucun projet n’est rédigé tant que la recherche n’est pas examinée.',
                                      'link': '/conflits?' + urlencode({'matter': p['matter']}), 'link_label': 'Examiner'})
    except Exception:
        pass
    return out


def _what(desk, args):
    """5.6.1 : objet précis d'un travail, affiché sous son libellé (nom de la pièce, document demandé, objet du courriel)."""
    if args.get('path'):
        return PurePosixPath(str(args['path'])).name
    if args.get('request') and _table(desk, 'docreq520'):
        row = desk.db.execute('SELECT request FROM docreq520 WHERE id=?', (str(args['request']),)).fetchone()
        if row:
            text = re.sub(r'\s+', ' ', row['request']).strip()
            return '« %s »' % (text[:90] + ('…' if len(text) > 90 else ''))
    key = args.get('key') or args.get('mail_key')
    if key:
        row = desk.db.execute('SELECT subject FROM work_items WHERE mail_key=?', (str(key),)).fetchone()
        if row and row['subject']:
            return '« %s »' % row['subject'][:90]
    return ''


def _table(desk, name):
    return bool(desk.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone())


def feed_html(desk, prefix, current='en_cours'):
    f = feed(desk)
    tabs = ''.join('<button type="button" class="c530-pill" data-feed="%s" aria-pressed="%s">%s · %d</button>' % (
        k, 'true' if k == current else 'false', label, len(f[k])) for k, label in (('en_cours', 'En cours'), ('fait', 'Fait'), ('bloque', 'Bloqué')))
    lists = ''
    for k in ('en_cours', 'fait', 'bloque'):
        rows = ''
        for a in f[k]:
            action = ''
            if a.get('retry'):
                action = '<button type="button" class="ax-btn ghost c530-small" data-retry="%d">Relancer</button>' % a['retry']
            elif a.get('link'):
                action = '<a class="ax-btn ghost c530-small" href="%s">%s</a>' % (e(prefix + a['link']), e(a['link_label']))
            title = ('<button type="button" class="c561-open" data-job="%d" title="Voir le détail">%s</button>' % (a['job'], e(a['text']))) if a.get('job') else (
                '<strong>%s</strong>' % e(a['text']))
            rows += ('<li class="c530-act %s"%s><span class="c530-dot" aria-hidden="true"></span><div><div class="c530-row">%s<span class="c530-when">%s</span></div>'
                     '%s%s%s%s</div></li>') % (
                k + (' running' if a.get('running') else '') + (' c561-clickable' if a.get('job') else ''),
                (' data-job="%d"' % a['job']) if a.get('job') else '', title, e(a['when']),
                ('<span class="c530-sub">%s</span>' % e(a['matter'])) if a['matter'] else '', ('<span class="c530-dest">%s</span>' % e(a['detail'])) if a['detail'] else '',
                '<span class="c530-bar" aria-hidden="true"><span></span></span>' if a.get('running') else '', action)
        empty = {'en_cours': 'Rien en cours : l’agent attend le prochain courriel ou votre instruction.', 'fait': 'Rien de terminé depuis 24 heures.',
                 'bloque': 'Aucun blocage.'}[k]
        lists += '<ul class="c530-list c530-feed" data-status="%s"%s>%s</ul>' % (k, '' if k == current else ' hidden', rows or '<li class="c530-empty">%s</li>' % e(empty))
    return '<div class="c530-head"><h2 id="c530-t-agent">Ce que fait l’agent</h2></div><div class="c530-pills" role="group" aria-label="Filtrer l’activité">%s</div>%s' % (tabs, lists)


def retry(desk, data):
    from .desk import JOBS
    try:
        jid = int(data.get('job'))
    except (TypeError, ValueError):
        raise Stop('travail_invalide') from None
    row = desk.db.execute("SELECT kind,args FROM jobs WHERE id=? AND status='error'", (jid,)).fetchone()
    if not row or row['kind'] not in JOBS or row['kind'] in NOISE:
        raise Stop('travail_invalide')
    job = desk.enqueue(row['kind'], json.loads(row['args'] or '{}'))
    return {'message': 'Relancé (travail n° %d).' % job}


# ======================================================================================== ma journée
def day_html(desk, prefix):
    labels = _labels(desk)
    z = tz(desk)
    today = datetime.now(z).date()
    start = datetime.combine(today, dtime.min, z).astimezone(timezone.utc).isoformat()
    end = datetime.combine(today + timedelta(days=1), dtime.min, z).astimezone(timezone.utc).isoformat()
    try:
        events = [dict(r) for r in desk.db.execute('SELECT id,title,starts,ends,matter FROM calendar_cache WHERE starts<? AND ends>? ORDER BY starts LIMIT 12', (end, start))]
    except Exception:
        events = []
    ressort = ''
    try:
        from . import routines520
        ressort = routines520.settings(desk).get('ressort', '')
    except Exception:
        pass
    ev_html = ''
    for ev in events:
        s = datetime.fromisoformat(ev['starts']).astimezone(z)
        when = 'Journée' if s.time() == dtime.min else s.strftime('%H:%M')
        title = ev['title'] or 'Événement'
        far = ressort and 'audience' in fold(title) and fold(ressort) not in fold(title) and re.search(r'\b(tj|tribunal|cour|ca|cph)\b', fold(title))
        ev_html += '<li class="c530-ev%s"><time>%s</time><div><strong>%s</strong>%s</div></li>' % (
            ' far' if far else '', e(when), e(title), ('<span class="c530-sub">%s</span>' % e(labels.get(ev['matter'], ''))) if ev['matter'] else '')
    try:
        tasks = [dict(r) for r in desk.db.execute("SELECT id,external_uid,title,due,status,matter,updated FROM work_tasks_v211 WHERE status NOT IN ('completed','cancelled') "
                                                  "ORDER BY CASE WHEN due='' THEN 1 ELSE 0 END, due LIMIT 8")]
        tasks += [dict(r) for r in desk.db.execute("SELECT id,external_uid,title,due,status,matter,updated FROM work_tasks_v211 WHERE status='completed' AND updated>=? "
                                                   "ORDER BY updated DESC LIMIT 3", (start,))]
    except Exception:
        tasks = []
    tk_html = ''
    for t in tasks:
        mine = not str(t['external_uid']).startswith('axiorhub-')
        due = ''
        if t['due']:
            try:
                dd = datetime.fromisoformat(t['due']).astimezone(z).date()
                due = 'aujourd’hui' if dd == today else ('demain' if dd == today + timedelta(days=1) else dd.strftime('%d/%m'))
                due = ('en retard · ' + due) if dd < today and t['status'] != 'completed' else due
            except ValueError:
                due = ''
        sub = ' · '.join(x for x in (labels.get(t['matter'], ''), due) if x)
        tk_html += ('<li class="c530-task"><input type="checkbox" id="c530-tk-%s" data-task="%s"%s><label for="c530-tk-%s"><span class="%s">%s</span>%s</label>'
                    '<span class="c530-by %s">%s</span></li>') % (
            e(t['id'], quote=True), e(t['id'], quote=True), ' checked' if t['status'] == 'completed' else '', e(t['id'], quote=True),
            'c530-done' if t['status'] == 'completed' else '', e(t['title']), ('<span class="c530-sub">%s</span>' % e(sub)) if sub else '',
            'me' if mine else 'agent', 'Vous' if mine else 'Agent')
    from . import deck530
    s = deck530.settings(desk)
    board = desk.settings('deck530:board_url', '') or ''
    deck_cols = ''.join('<div class="c530-deckcol c%d"><strong>%d</strong><span>%s</span></div>' % (i, n, e(name)) for i, (name, n) in enumerate(deck530.counts(desk)))
    last = desk.settings('deck530:last', {}) or {}
    status = ('Dernière synchronisation : %s.' % e(_local(desk, last.get('at', '')))) if last.get('at') else ('Désactivé.' if not (s['deck'] or s['tasks']) else 'Première synchronisation dans 5 minutes au plus.')
    settings_form = ('<details class="c530-settings"><summary>Réglages Deck et Tâches</summary><form class="m5-form" data-api="m530/nextcloud" data-reload="1">'
                     '<label class="m5-check"><input type="checkbox" name="deck"%s> Tableau Deck du travail de l’agent</label>'
                     '<label class="m5-check"><input type="checkbox" name="tasks"%s> Une tâche « Relire : … » par document produit</label>'
                     '<label class="m5-field">Titre du tableau<input name="board" maxlength="80" value="%s"></label>'
                     '<label class="m5-field">Partager le tableau avec le compte Nextcloud<input name="share_with" maxlength="64" value="%s" placeholder="votre identifiant Nextcloud"></label>'
                     '<p class="c530-note">AxiorHub n’écrit que dans son tableau et sa liste de tâches, sans jamais rien supprimer.</p>'
                     '<button class="ax-btn" type="submit">Enregistrer</button></form>'
                     '<form class="m5-form m5-inline" data-api="m530/nextcloud/sync" data-reload="1"><button class="ax-btn ghost" type="submit">Synchroniser maintenant</button></form></details>') % (
        ' checked' if s['deck'] else '', ' checked' if s['tasks'] else '', e(s['board'], quote=True), e(s['share_with'], quote=True))
    return ('<div class="c530-head"><h2 id="c530-t-day">Ma journée</h2><a href="%s">Agenda</a></div>%s'
            '<div class="c530-head sub"><h3>Tâches Nextcloud</h3><a href="%s">Toutes les tâches</a></div>%s'
            '<div class="c530-head sub"><h3>Deck · %s</h3>%s</div><div class="c530-deck">%s</div><p class="c530-note">%s</p>%s') % (
        e(prefix + '/planning?vue=agenda'), ('<ul class="c530-list">%s</ul>' % ev_html) if ev_html else '<p class="c530-empty">Rien à l’agenda aujourd’hui.</p>',
        e(prefix + '/planning?vue=taches'), ('<ul class="c530-list">%s</ul>' % tk_html) if tk_html else '<p class="c530-empty">Aucune tâche ouverte.</p>',
        e(s['board']), ('<a target="_blank" rel="noopener noreferrer" href="%s">Ouvrir dans Deck</a>' % e(board, quote=True)) if board.startswith('https://') else '',
        deck_cols, status, settings_form)


def toggle_task(desk, data):
    tid, done = str(data.get('task') or ''), bool(data.get('done'))
    row = desk.db.execute('SELECT external_uid FROM work_tasks_v211 WHERE id=?', (tid,)).fetchone()
    if not row:
        raise Stop('tache_absente')
    status = 'completed' if done else 'todo'
    if str(row['external_uid']).startswith('axiorhub-'):
        from .workplan import update_task_status
        update_task_status(desk, {'task': tid, 'status': status})
    else:
        from . import agenda520
        agenda520.edit_task(desk, {'task': tid, 'status': status})
    return {'message': 'Tâche cochée dans Nextcloud.' if done else 'Tâche rouverte dans Nextcloud.'}


# ======================================================================================== routines et style
def routines_html(desk, prefix):
    from . import routines520 as r5
    tabs = [('briefing', 'Briefing du matin'), ('tri', 'Tri des courriels'), ('bilan', 'Bilan de la semaine'), ('docs', 'Documents à préparer')]
    panels = ''
    for kind, name in tabs[:3]:
        rep = r5.latest(desk, kind)
        if rep:
            body = '<p class="c530-note">%s</p>%s' % (e('Produit le ' + _local(desk, rep['created'], '%H:%M')), r5.render_markdown(rep['text']))
            body += ''.join('<p class="c530-note">%s</p>' % e(n) for n in (rep['notes'] or []))
        else:
            body = '<p class="c530-empty">Pas encore produit.</p>'
        body += '<form class="m5-form m5-inline" data-api="m530/routine" data-reload="1"><input type="hidden" name="kind" value="%s"><button class="ax-btn ghost" type="submit">Générer maintenant</button></form>' % kind
        panels += '<div class="c530-panel" role="tabpanel" data-panel="%s"%s>%s</div>' % (kind, '' if kind == 'briefing' else ' hidden', body)
    labels = _labels(desk)
    docs = ''
    try:
        for d in r5.documents(desk)[:12]:
            due = (' — échéance ' + d['due'][8:10] + '/' + d['due'][5:7]) if d['due'] else ''
            action = ('<button type="button" class="ax-btn ghost c530-small" data-prepare="%s" data-matter="%s">Préparer avec l’IA</button>' % (
                e(d['instruction'] or d['title'], quote=True), e(d['matter'], quote=True))) if d['matter'] else '<span class="c530-note">dossier à préciser</span>'
            docs += '<li class="c530-todo"><div><strong>%s</strong><span class="c530-sub">%s%s</span></div>%s</li>' % (
                e(d['title']), e(labels.get(d['matter'], d['source'])), e(due), action)
    except Exception:
        pass
    panels += '<div class="c530-panel" role="tabpanel" data-panel="docs" hidden>%s</div>' % (
        ('<ul class="c530-list">%s</ul>' % docs) if docs else '<p class="c530-empty">Aucun document en attente.</p>')
    buttons = ''.join('<button type="button" role="tab" class="c530-pill" data-tab="%s" aria-selected="%s">%s</button>' % (k, 'true' if k == 'briefing' else 'false', e(n)) for k, n in tabs)
    return ('<div class="c530-head"><h2 id="c530-t-routines">Routines du cabinet</h2><div class="c530-pills" role="tablist" aria-label="Routines">%s</div></div>%s'
            '<p class="c530-note"><a href="%s">Heures, jours, signature et exclusions</a></p>') % (buttons, panels, e(prefix + '/aujourdhui?vue=essentiel'))


def style_html(desk, prefix):
    """5.5.0 : corpus analysé, habitudes à valider (boutons), part des brouillons envoyés sans retouche."""
    from . import style550
    o = style550.overview(desk)
    tel_quel = total = None
    try:
        from . import learning480
        t = learning480.dashboard(desk, months=1)['total']
        total, tel_quel = t['total'], t['pct'].get('tel_quel')
    except Exception:
        pass
    pending = ''.join(
        '<li class="c530-habit"><span class="c530-type">%s</span><p>%s</p><span class="c530-sub">%s</span>'
        '<div class="c530-actions"><button type="button" class="ax-btn ghost c530-small" data-habit="%s" data-action="valider">Valider</button>'
        '<button type="button" class="ax-btn ghost c530-small" data-habit="%s" data-action="ecarter">Écarter</button></div></li>' % (
            e(style550.TYPES.get(h['doc_type'], h['doc_type'])), e(h['text']), e(h['evidence']), e(h['id'], quote=True), e(h['id'], quote=True))
        for h in o['pending'][:3])
    stats = ''.join('<div><strong>%d</strong><span>%s</span></div>' % (n, e(label)) for n, label in (
        (o['files'], 'écrits des dossiers'), (o['mails'], 'courriels envoyés'), (len(o['validated']), 'habitudes appliquées'), (len(o['pending']), 'à valider')))
    rate = ('<p class="c530-note">%s %% des brouillons du mois envoyés sans retouche (%d rapproché(s)).</p>' % (
        ('%g' % tel_quel) if tel_quel is not None else '—', total)) if total else ''
    return ('<div class="c530-head"><h2 id="c530-t-style">Style du cabinet</h2><a href="%s">Mon style</a></div><div class="c530-stats">%s</div>'
            '<h3>Habitudes à valider</h3>%s%s') % (
        e(prefix + '/mon-style'), stats,
        ('<ul class="c530-list">%s</ul>' % pending) if pending else '<p class="c530-empty">Rien à valider. L’agent analyse vos écrits chaque nuit.</p>', rate)


# ======================================================================================== page
def header_html(desk):
    running = desk.db.execute("SELECT COUNT(*) FROM jobs WHERE status='running'").fetchone()[0]
    pending = desk.db.execute("SELECT COUNT(*) FROM jobs WHERE status='pending'").fetchone()[0]
    nxt = ''
    try:
        row = desk.db.execute("SELECT next_check FROM live_services_v430 WHERE name='surveillance'").fetchone()
        if row and row['next_check']:
            secs = float(row['next_check']) - time.time()
            nxt = ' · prochain contrôle des courriels %s' % ('dans %d min' % max(1, int(secs // 60)) if secs > 0 else 'imminent')
    except Exception:
        pass
    alert = ''
    try:
        from .web520 import diagnosis
        d = diagnosis(desk)
        if d['problems']:
            alert = '<a class="c530-alert" href="diagnostic">%s</a>' % e(d['problems'][0])
    except Exception:
        pass
    return '<span class="c530-status"><span class="c530-live" aria-hidden="true"></span>%d en cours · %d en file%s</span>%s' % (running, pending, e(nxt), alert)


def page(desk, prefix, csrf=''):
    ensure_schema(desk)
    z = tz(desk)
    days = ('lundi', 'mardi', 'mercredi', 'jeudi', 'vendredi', 'samedi', 'dimanche')
    months = ('janvier', 'février', 'mars', 'avril', 'mai', 'juin', 'juillet', 'août', 'septembre', 'octobre', 'novembre', 'décembre')
    now = datetime.now(z)
    when = '%s %d %s %d' % (days[now.weekday()].capitalize(), now.day, months[now.month - 1], now.year)
    options = ''.join('<option value="%s">%s</option>' % (e(mid, quote=True), e(label)) for mid, label in sorted(_labels(desk).items(), key=lambda kv: fold(kv[1])))
    header = header_html(desk).replace('href="diagnostic"', 'href="%s"' % e(prefix + '/diagnostic', quote=True))
    return ('<div class="c530" id="c530" data-prefix="%s">'
            '<header class="c530-top"><div><p class="c530-date">%s</p><h1>Aujourd’hui</h1></div><div class="c530-statusbar" id="c530-header">%s</div></header>'
            '<section class="c530-card c530-composer" aria-label="Conversation avec l’agent">%s'
            '<label class="c530-sr" for="c530-text">Instruction à l’agent</label>'
            '<textarea id="c530-text" rows="3" maxlength="4000" placeholder="Demandez à l’agent… ex. « Prépare la réponse au confrère sur le calendrier de procédure dans le dossier LEROY »"></textarea>'
            '<div class="c530-bar2"><div class="c530-dossier"><span class="c530-sub">Dossier :</span> <strong id="c530-dossier-label">à deviner d’après votre demande</strong> '
            '<span class="c530-badge" id="c530-dossier-how">en attente</span> <label class="c530-sub">Corriger <select id="c530-override"><option value="">Laisser l’agent deviner</option>%s</select></label>'
            '<span id="c530-files"></span></div>'
            '<div class="c530-tools"><input type="file" id="c530-file" hidden multiple accept=".pdf,.docx,.odt,.txt,.md,.csv,.eml">'
            '<button type="button" class="c530-icon" id="c530-attach" aria-label="Joindre un document" title="Joindre un document (PDF, Word, texte, courriel)">'
            '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M12 5v14M5 12h14"/></svg></button>'
            '<button type="button" class="c530-icon" id="c530-mic" aria-label="Dicter" title="Dicter" aria-pressed="false">'
            '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5 11a7 7 0 0 0 14 0M12 18v3"/></svg></button>'
            '<button type="button" class="ax-btn c530-send" id="c530-send"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true">'
            '<path d="M4 12l16-8-6 16-2-7z"/></svg>Envoyer</button></div></div>'
            '<p class="c530-note">L’agent continue son travail automatique pendant la conversation. Rien n’est envoyé, déposé ni signé sans vous. Ctrl+Entrée pour envoyer.</p></section>'
            '<div class="c530-cols"><section class="c530-card" id="c530-review" aria-labelledby="c530-t-review">%s</section>'
            '<section class="c530-card" id="c530-feed" aria-labelledby="c530-t-agent">%s</section>'
            '<section class="c530-card" id="c530-day" aria-labelledby="c530-t-day">%s</section></div>'
            '<div class="c530-split"><section class="c530-card" id="c530-routines" aria-labelledby="c530-t-routines">%s</section>'
            '<section class="c530-card" id="c530-style" aria-labelledby="c530-t-style">%s</section></div>'
            '<p class="c530-note"><a href="%s">Vue « essentiel du jour » (5.2)</a> · <a href="%s">Ancien cockpit</a></p>'
            '<div class="c530-drawer" id="c530-drawer" hidden><div class="c530-backdrop" data-close="1" aria-hidden="true"></div>'
            '<div class="c530-panelside" role="dialog" aria-modal="true" aria-labelledby="c530-drawer-title"><button type="button" class="c530-icon c530-close" data-close="1" aria-label="Fermer">'
            '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18"/></svg></button>'
            '<div id="c530-drawer-content"></div></div></div></div>') % (
        e(prefix, quote=True), e(when), header, thread_html(desk, prefix), options,
        review_html(desk, prefix), feed_html(desk, prefix), day_html(desk, prefix), routines_html(desk, prefix), style_html(desk, prefix),
        e(prefix + '/aujourdhui?vue=essentiel'), e(prefix + '/aujourdhui?vue=cockpit'))


PARTS = {'thread': thread_html, 'review': review_html, 'feed': feed_html, 'day': day_html}


def handle(desk, name, data, method='POST', args=None):
    n = name[len('m530/'):]
    args = dict(args or {})
    # 5.6.1 : préfixe d'adresse de l'interface (ex. /agent-courriel) pour les liens des fragments ; seul un chemin simple est accepté.
    if not re.fullmatch(r'(?:/[A-Za-z0-9_-]{1,40}){0,3}', str(args.get('prefix') or '')):
        args['prefix'] = ''
    if method == 'GET':
        if n == 'guess':
            return guess(desk, args.get('text', ''))
        if n == 'part':
            part = args.get('name', '')
            prefix = str(args.get('prefix') or '')
            if part == 'header':
                return {'html': header_html(desk)}
            if part not in PARTS:
                raise Stop('route_inconnue')
            return {'html': PARTS[part](desk, prefix)}
        if n == 'item':
            return {'html': item_html(desk, str(args.get('prefix') or ''), str(args.get('id') or ''))}
        if n == 'job':
            from . import jobview561
            return {'html': jobview561.job_html(desk, str(args.get('prefix') or ''), args.get('id'))}
        if n == 'open':
            from . import jobview561
            return jobview561.open_url(desk, args.get('path', ''), args.get('matter', ''))
        raise Stop('route_inconnue')
    if n == 'ask':
        return ask(desk, data)
    if n == 'review':
        return review(desk, data)
    if n == 'revise':
        return revise(desk, data)
    if n == 'retry':
        return retry(desk, data)
    if n == 'stop':
        return stop(desk, data)
    if n == 'clear':
        return clear(desk)
    if n == 'cancel':
        return cancel(desk, data)
    if n == 'task':
        return toggle_task(desk, data)
    if n == 'routine':
        kind = str(data.get('kind') or '')
        from . import routines520
        if kind not in routines520.KINDS:
            raise Stop('routine_inconnue')
        job = desk.enqueue('routine520', {'kind': kind})
        return {'message': 'Génération lancée (travail n° %d).' % job}
    if n == 'nextcloud':
        from . import deck530
        return deck530.save_settings(desk, data)
    if n == 'nextcloud/sync':
        job = desk.enqueue('deck530_sync', {}, priority=0)
        return {'message': 'Synchronisation lancée (travail n° %d).' % job}
    raise Stop('route_inconnue')
