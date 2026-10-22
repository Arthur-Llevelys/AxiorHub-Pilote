"""5.6.36 : « Points à vérifier » — des constats calmes, chacun avec le bouton qui le règle.

Avant : un bandeau rouge en tête d'« Aujourd'hui » (« Surveillance … suspendue après trois échecs ») renvoyait vers le diagnostic, où
aucun bouton ne résolvait les points listés. Désormais :
- l'en-tête d'« Aujourd'hui » n'affiche qu'un petit indicateur neutre (« 2 points à vérifier ») ;
- le détail est en bas de page, replié, avec pour chaque point l'action qui le règle (relancer la surveillance, écarter des erreurs
  déjà vues, réessayer ou ne plus surveiller un dossier illisible, désengorger la file) ;
- la page « Pourquoi rien n'est produit ? » reprend les mêmes boutons.
Rien n'est supprimé : une erreur « écartée » reste dans l'historique ; un dossier retiré de la surveillance peut y revenir.
"""
from datetime import datetime, timedelta, timezone
from html import escape as e
import json

from .common import Stop

ACK = 'sante5636:erreurs_vues'   # date ISO jusqu'à laquelle l'avocat a pris connaissance des erreurs


def _now():
    return datetime.now(timezone.utc)


def checks(desk):
    """Liste de points : {'id', 'text', 'level' (info | attention), 'actions': [{'api', 'label', 'data', 'confirm'}]}."""
    from .web520 import diagnosis
    from .watch430 import unreadable
    d = diagnosis(desk)
    out = []
    for text in d['problems']:
        item = {'id': '', 'text': text, 'level': 'attention', 'actions': []}
        if 'suspendue après trois échecs' in text:
            item.update(id='surveillance', actions=[{'api': 'm520/queue/resume', 'label': 'Relancer la surveillance'}])
        elif 'en erreur depuis 24 heures' in text:
            item.update(id='erreurs', level='info', actions=[
                {'api': 'm5636/erreurs/ecarter', 'label': 'Écarter ces erreurs (déjà vues)',
                 'confirm': 'Écarter les erreurs des 7 derniers jours ? Elles restent dans l’historique ; les nouvelles erreurs seront de nouveau signalées.'},
                {'href': 'diagnostic#erreurs', 'label': 'Voir les motifs'}])
        elif 'demande(s) en attente' in text:
            item.update(id='file', actions=[{'href': 'diagnostic#file', 'label': 'Voir la file de travail'}])
        out.append(item)
    try:   # 5.6.70 : PHP du serveur non corrigé contre « PHP2Shell »
        from .php5670 import health_item, host_status
        php = health_item(host_status())
        if php:
            out.append(php)
    except Exception:   # noqa: BLE001
        pass
    gone = unreadable(desk)
    if gone:
        out.append({'id': 'illisibles', 'level': 'info', 'text': '%d dossier(s) n’ont pas pu être lus par la surveillance des documents.' % len(gone),
                    'actions': [{'href': 'diagnostic#illisibles', 'label': 'Voir les dossiers concernés'}]})
    return out


def _button(a, prefix):
    if a.get('href'):
        return '<a class="ax-btn ghost" href="%s">%s</a>' % (e(prefix + '/' + a['href'], quote=True), e(a['label']))
    return ('<form class="m5-form m5-inline m5-mini" data-api="%s" data-reload="1"%s>%s<button class="ax-btn ghost" type="submit">%s</button></form>') % (
        e(a['api'], quote=True), (' data-confirm="%s"' % e(a['confirm'], quote=True)) if a.get('confirm') else '',
        ''.join('<input type="hidden" name="%s" value="%s">' % (e(k, quote=True), e(v, quote=True)) for k, v in (a.get('data') or {}).items()),
        e(a['label']))


def items_html(items, prefix):
    return '<ul class="s5636-list">%s</ul>' % ''.join(
        '<li class="s5636-%s"><span>%s</span><span class="s5636-actions">%s</span></li>' % (
            'info' if x['level'] == 'info' else 'attention', e(x['text']), ''.join(_button(a, prefix) for a in x['actions']))
        for x in items)


def indicator_html(desk, prefix):
    """En-tête d'« Aujourd'hui » : indicateur neutre (plus de bandeau rouge)."""
    try:
        n = len(checks(desk))
    except Exception:   # noqa: BLE001 — l'en-tête ne doit jamais casser la page
        return ''
    if not n:
        return ''
    return '<a class="s5636-indicator" href="#s5636-checks" data-s5636-open>%d point%s à vérifier</a>' % (n, 's' if n > 1 else '')


def panel_html(desk, prefix):
    """Bas d'« Aujourd'hui » : détail replié, chaque point avec son bouton."""
    try:
        items = checks(desk)
    except Exception:   # noqa: BLE001
        return ''
    if not items:
        return ''
    return ('<details class="s5636-panel" id="s5636-checks"><summary>Points à vérifier (%d) — rien d’urgent : AxiorHub continue de travailler'
            '</summary><p class="s5636-help">Chaque point a son bouton. Le détail complet est dans « <a href="%s">Pourquoi rien n’est produit ?</a> ».</p>%s'
            '</details>') % (len(items), e(prefix + '/diagnostic', quote=True), items_html(items, prefix))


def unreadable_html(desk, prefix):
    """Diagnostic : dossiers que la surveillance n'a pas pu lire, avec Réessayer / Ne plus surveiller ; dossiers retirés (Rétablir)."""
    from .watch430 import ignored, unreadable
    from .web520 import _reason
    from .common import load_matters, matter_display
    gone, skip = unreadable(desk), ignored(desk)
    if not gone and not skip:
        return ''
    labels = {}
    try:
        labels = {m['id']: matter_display(m) for m in load_matters(desk.c)}
    except Stop:
        pass
    rows = ''.join(
        '<tr><td>%s</td><td>%s</td><td>%s</td><td>%s%s</td></tr>' % (
            e(x.get('label') or labels.get(mid, mid)), e(_reason(x.get('code', ''))), e(str(x.get('at', ''))[:16].replace('T', ' ')),
            _button({'api': 'm5636/dossier/reessayer', 'label': 'Réessayer', 'data': {'matter': mid}}, prefix),
            _button({'api': 'm5636/dossier/ignorer', 'label': 'Ne plus surveiller', 'data': {'matter': mid},
                     'confirm': 'Ne plus surveiller ce dossier ? Ses documents ne seront plus analysés automatiquement ; vous pourrez le rétablir ici.'}, prefix))
        for mid, x in sorted(gone.items(), key=lambda kv: kv[1].get('label', '')))
    html = ('<section class="ax-card" id="illisibles"><h2>Dossiers non lus par la surveillance des documents</h2><p class="vf-note">Un dossier '
            'illisible (supprimé, renommé, droits retirés) est passé ; il ne bloque plus la surveillance des autres. Corrigez-le dans Nextcloud puis '
            '« Réessayer », ou retirez-le de la surveillance.</p>')
    if rows:
        html += '<table class="vf-table"><thead><tr><th>Dossier</th><th>Motif</th><th>Dernier essai</th><th>Action</th></tr></thead><tbody>%s</tbody></table>' % rows
    if skip:
        html += '<h3>Retirés de la surveillance</h3><ul>%s</ul>' % ''.join(
            '<li>%s %s</li>' % (e(labels.get(mid, mid)), _button({'api': 'm5636/dossier/retablir', 'label': 'Surveiller de nouveau', 'data': {'matter': mid}}, prefix))
            for mid in skip)
    return html + '</section>'


def dismiss_errors(desk, owner='cabinet'):
    """Écarte les erreurs des 7 derniers jours (statut « écarté », motif conservé) ; elles ne comptent plus dans les points à vérifier."""
    since = (_now() - timedelta(days=7)).isoformat()
    rows = desk.db.execute("SELECT id,result FROM jobs WHERE status='error' AND COALESCE(finished,created)>=?", (since,)).fetchall()
    for r in rows:
        try:
            result = json.loads(r['result'] or '{}')
        except ValueError:
            result = {}
        result = result if isinstance(result, dict) else {}
        result['ecarte_le'] = _now().isoformat()
        desk.db.execute("UPDATE jobs SET status='cancelled',result=? WHERE id=? AND status='error'", (json.dumps(result, ensure_ascii=False), r['id']))
    desk.db.commit()
    desk.audit('sante5636_erreurs_ecartees', {'nombre': len(rows), 'owner': owner})
    return {'dismissed': len(rows), 'message': '%d erreur(s) écartée(s) ; elles restent dans l’historique.' % len(rows)}


def dismiss_job(desk, job_id, owner='cabinet'):
    """« Écarter » une demande en erreur (panneau d'activité) : elle ne sera ni relancée ni signalée."""
    try:
        job_id = int(job_id)
    except (TypeError, ValueError):
        raise Stop('operation_invalide') from None
    row = desk.db.execute('SELECT status,result,kind FROM jobs WHERE id=?', (job_id,)).fetchone()
    if not row:
        raise Stop('operation_absente')
    if row['status'] != 'error':
        raise Stop('operation_non_en_erreur')
    try:
        result = json.loads(row['result'] or '{}')
    except ValueError:
        result = {}
    result = result if isinstance(result, dict) else {}
    result['ecarte_le'] = _now().isoformat()
    desk.db.execute("UPDATE jobs SET status='cancelled',result=? WHERE id=? AND status='error'", (json.dumps(result, ensure_ascii=False), job_id))
    desk.db.commit()
    desk.audit('sante5636_travail_ecarte', {'job': job_id, 'kind': row['kind'], 'owner': owner})
    return {'job_id': job_id, 'status': 'cancelled', 'message': 'Demande écartée ; elle reste dans l’historique.'}
