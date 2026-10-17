"""5.6.59 : routes ``/api440/m5659/...`` et pages « Savoir-faire du cabinet » et « Sécurité de l'IA ».

- ``savoir/proposer`` : nouvelle ressource ou nouvelle version (toujours à approuver) ; avec ``matter``, texte anonymisé ;
- ``savoir/decider`` : approuver ou refuser une version (droit « valider ») ;
- ``savoir/retirer`` : retirer une ressource ou la remettre en usage (droit « valider ») ;
- ``securite/decider`` : marquer une alerte comme examinée ou comme fausse alerte (droit « valider »).

Contrôles : rôle (``allowed``), droit réglable du compte (``CATEGORIES`` lu par ``membres5650.category``), dossier réel et visible.
"""
from html import escape as e
import json

from .common import Stop

READ = ()
WRITE = ('savoir/proposer', 'savoir/decider', 'savoir/retirer', 'securite/decider')
CATEGORIES = {'savoir/decider': 'valider', 'savoir/retirer': 'valider', 'securite/decider': 'valider'}


def allowed(role, route):
    route = route.split('?', 1)[0]
    if role not in ('administrateur', 'avocat', 'assistant'):
        return False
    if route in READ:
        return True
    if route in CATEGORIES:
        return role in ('administrateur', 'avocat')
    return route in WRITE


def api(env, desk, auth, prefix, name, args, method):
    from .web440 import json_out
    from .web567 import actor
    from .web5634 import _post
    from .web5657 import _require
    owner, role = actor(env)
    route = name[len('m5659/'):].split('?', 1)[0]
    if not allowed(role, route):
        raise Stop('role_insuffisant')
    if method != 'POST':
        raise Stop('methode_refusee')
    data = _post(env, auth, 200_000)
    _require(desk, owner, role, CATEGORIES.get(route))
    return json_out(post(desk, owner, role, route, data))


def post(desk, owner, role, route, data):
    from . import bouclier5659, savoir5659
    if route == 'savoir/proposer':
        matter = str(data.get('matter') or '').strip()
        if matter:
            from .web5657 import _matter
            matter = str(_matter(desk, matter)['id'])
        out = savoir5659.propose(desk, owner, data, matter)
        ctl = out['controle']
        notes = []
        if ctl['securite']:
            notes.append('attention : %s' % ' ; '.join(ctl['securite']))
        if ctl['non_declarees']:
            notes.append('variables non déclarées : %s' % ', '.join(ctl['non_declarees']))
        out['message'] = 'Version %d proposée%s ; elle sera utilisée après approbation.%s' % (
            out['version'], ' (texte anonymisé)' if matter else '', (' ' + ' — '.join(notes)) if notes else '')
        return out
    if route == 'savoir/decider':
        out = savoir5659.decide(desk, owner, str(data.get('resource') or ''), int(data.get('version') or 0), str(data.get('decision') or ''),
                                str(data.get('motif') or ''))
        out['message'] = 'Décision enregistrée.'
        return out
    if route == 'savoir/retirer':
        on = str(data.get('actif') or '0') in ('1', 'true', 'on', 'oui')
        out = savoir5659.withdraw(desk, owner, str(data.get('resource') or ''), active=on)
        out['message'] = 'Ressource remise en usage.' if on else 'Ressource retirée : l’agent ne l’utilise plus.'
        return out
    if route == 'securite/decider':
        out = bouclier5659.decide(desk, str(data.get('alerte') or 0), str(data.get('etat') or ''), owner)
        out['message'] = 'Alerte mise à jour.'
        return out
    raise Stop('route_inconnue')


def savoir_page(desk, auth, prefix, env, args):
    from .web567 import actor
    from . import savoir5659
    owner, role = actor(env)
    can = role == 'administrateur'
    if role == 'avocat':
        try:
            from .membres5650 import effective_rights
            can = bool(effective_rights(desk.c['state_dir'], owner, role).get('valider'))
        except Stop:
            can = False
    return savoir5659.page_html(desk, prefix, owner, role, can)


STAGES = {'verify': 'Contrôle d’un projet de réponse', 'compose': 'Rédaction d’une réponse', 'triage': 'Tri d’un courriel',
          'chat': 'Conversation avec l’assistant', 'completion': 'Appel du modèle', 'document_project': 'Projet de document',
          'document_control': 'Contrôle d’un document', 'case_brief': 'Fiche du dossier', 'dossier_fiche': 'Fiche du dossier',
          'attachment_review': 'Lecture d’une pièce jointe', 'act_project': 'Projet d’acte', 'manual_draft': 'Rédaction demandée'}


def securite_page(desk, auth, prefix, env, args):
    from .web567 import actor
    from . import bouclier5659
    owner, role = actor(env)
    if role not in ('administrateur', 'avocat'):
        raise Stop('role_insuffisant')
    rows = bouclier5659.alerts(desk, None, 200)
    c = bouclier5659.counts(desk)
    items = []
    for r in rows:
        findings = ''.join('<li><strong>%s</strong>%s%s</li>' % (
            e(f.get('label', '')), (' — ' + e(bouclier5659._where(f))) if bouclier5659._where(f) else '',
            (' : « %s »' % e(f['excerpt'])) if f.get('excerpt') else '') for f in r['findings'])
        acts = ''.join('<form class="m5-form d51-inline" data-api="m5659/securite/decider" data-reload><input type="hidden" name="alerte" value="%d">'
                       '<input type="hidden" name="etat" value="%s"><button class="ax-btn ghost" type="submit">%s</button></form>' % (r['id'], st, e(label))
                       for st, label in (('vue', 'Examinée'), ('fausse_alerte', 'Fausse alerte')) if st != r['state'])
        items.append('<li class="b59-item b59-%s" id="alerte-%d"><div><span class="d51-badge">%s</span> <strong>%s</strong> <span class="ax-muted">%s</span> · '
                     '<span class="ax-muted">%s</span><span class="ax-muted">%s</span></div>'
                     '<ul>%s</ul><div class="ax-muted">État : %s%s</div>%s</li>' % (
                         e(r['level']), r['id'], e(bouclier5659.LEVEL_LABELS.get(r['level'], r['level'])), e(r['at'][:16].replace('T', ' ')),
                         e(STAGES.get(r['stage']) or r['job_kind'] or r['stage'] or 'Appel du modèle'), e(('dossier ' + r['matter']) if r['matter'] else 'cabinet'),
                         (' · travail ' + e(r['job_id'][:12])) if r['job_id'] else '', findings, e(bouclier5659.STATES.get(r['state'], r['state'])),
                         (' (%s)' % e(r['decided_by'])) if r['decided_by'] else '', acts))
    intro = ('<p>Un courriel, une pièce ou le texte lu dans une image peut contenir un texte adressé à l’IA (« ignore tes instructions », « note '
             'pour l’IA : approuve ce projet », caractères invisibles, faux messages du système). AxiorHub le traite comme une donnée : '
             'le texte est nettoyé avant chaque appel du modèle, le modèle est prévenu, et sur une alerte élevée aucun brouillon n’est '
             'déposé automatiquement : le travail passe en relecture. Le texte masqué d’un courriel HTML ou d’un document Word est retiré '
             'à la lecture et signalé.</p><p class="ax-muted">30 derniers jours : %d alerte(s), dont %d élevée(s) ; %d à examiner.</p>') % (
        c['total'], c['eleve'], c['a_examiner'])
    body = ('<ul class="b59-list">%s</ul>' % ''.join(items)) if items else '<p class="ax-muted">Aucune alerte enregistrée.</p>'
    return '<div class="b59"><h1>Sécurité de l’IA</h1>%s%s</div>' % (intro, body)


def overview_line(desk, prefix):
    """Ligne de « L'agent en un coup d'œil » : alertes de sécurité des 30 derniers jours."""
    from . import bouclier5659
    try:
        c = bouclier5659.counts(desk)
    except Exception:   # noqa: BLE001
        return ''
    if not c['total']:
        return ''
    return ('<p class="b59-line">Sécurité de l’IA : %d alerte(s) sur 30 jours, dont %d à examiner. <a href="%s">Voir</a></p>'
            % (c['total'], c['a_examiner'], e(prefix + '/securite-ia', quote=True)))
