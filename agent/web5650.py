"""5.6.50 : pages et API de la messagerie interne (/messagerie) et des droits par compte (/parametres/droits)."""
from html import escape as e
import json

from .common import Stop

ADMIN_ROUTES = ('droits', 'reserve')


def allowed(role, route):
    """Messagerie : tous les rôles ; droits et dossiers réservés : administrateur seulement."""
    if role not in ('administrateur', 'avocat', 'assistant'):
        return False
    return role == 'administrateur' if route.split('?', 1)[0] in ADMIN_ROUTES else True


def _me(env, desk, auth, members=None):
    from .web567 import actor
    owner, role = actor(env)
    members = members if members is not None else _members(desk, auth)
    user = env.get('axiorhub.user') or {}
    name = (members.get(owner) or {}).get('name') or user.get('name') or owner
    return owner, role, name


def _members(desk, auth):
    from .membres5650 import directory
    return directory(desk, auth)


def _hidden():
    from .membres5650 import viewer_hidden
    return viewer_hidden()


# ------------------------------------------------------------------------------------------------ pages
def messagerie_page(desk, auth, prefix, env, args):
    from . import messagerie5650 as mx
    mx.ensure_schema(desk)
    members = _members(desk, auth)
    me, role, _ = _me(env, desk, auth, members)
    hidden = _hidden()
    tid = ''
    if args.get('avec'):
        tid = mx.open_direct(desk, me, args['avec'], members)
    elif args.get('dossier'):
        tid = mx.matter_thread(desk, str(args['dossier']), me, hidden, follow=False)
    elif args.get('fil'):
        tid = mx._check(desk, args['fil'], me, hidden)['id']
    others = sorted((m for o, m in members.items() if o != me), key=lambda m: (m['name'] or '').lower())
    options = ''.join('<option value="%s">%s</option>' % (e(m['owner'], quote=True), e('%s (%s)' % (m['name'] or m['owner'], m['role']))) for m in others)
    checks = ''.join('<label class="m50-check"><input type="checkbox" name="%s"> %s</label>' % (e('compte:' + m['owner'], quote=True), e(m['name'] or m['owner']))
                     for m in others)
    side = ('<details class="m50-new"><summary>Nouvelle conversation</summary><form class="m5-form" data-api="m5650/direct" data-reload>'
            '<label>Avec<select name="avec" required>%s</select></label><button class="ax-btn" type="submit">Ouvrir</button>'
            '<p role="status" class="m5-status"></p></form></details>'
            '<details class="m50-new"><summary>Nouveau groupe</summary><form class="m5-form" data-api="m5650/groupe" data-reload>'
            '<label>Nom du groupe<input name="titre" required maxlength="120" placeholder="Ex. Pôle contentieux"></label><div class="m50-checks">%s</div>'
            '<button class="ax-btn" type="submit">Créer le groupe</button><p role="status" class="m5-status"></p></form></details>') % (
        options or '<option value="">Aucun autre membre connu</option>', checks or '<p class="ax-muted">Aucun autre membre connu.</p>')
    main = ('<section class="m50-main" id="m50-main" aria-live="polite"><p class="m50-empty">Choisissez une conversation, ou ouvrez le fil d’un dossier '
            'depuis sa page.</p></section>') if not tid else (
        '<section class="m50-main" id="m50-main"><header class="m50-head"><h2 id="m50-title">…</h2><p class="m50-people" id="m50-people"></p>'
        '<div class="m50-tools" id="m50-tools"></div></header><ol class="m50-messages" id="m50-messages" aria-live="polite"></ol>'
        '<form class="m50-compose" id="m50-compose"><label for="m50-text" class="m50-sr">Votre message</label>'
        '<textarea id="m50-text" rows="3" maxlength="8000" required placeholder="Écrire un message… (@identifiant pour mentionner)"></textarea>'
        '<button class="ax-btn" type="submit">Envoyer</button><p role="status" class="m50-status" id="m50-status"></p></form></section>')
    body = ('<div class="m50"><h1>Messagerie du cabinet</h1><p class="ax-muted">Conversations entre membres du cabinet, sur le serveur du cabinet. '
            'L’agent peut y déposer des avis ; il ne lit pas les conversations.</p>'
            '<div class="m50-chat" id="m50-chat" data-fil="%s" data-me="%s"><aside class="m50-side">%s<h2 class="m50-sr">Conversations</h2>'
            '<ul class="m50-list" id="m50-list"></ul></aside>%s</div></div>') % (e(str(tid), quote=True), e(me, quote=True), side, main)
    return body


def droits_page(desk, auth, prefix, env, args):
    from .membres5650 import page_html
    return page_html(desk, auth, prefix, args)


def matter_card(desk, auth, prefix, mid, role, me):
    """Page du dossier : fil de discussion (non lus) et, pour l'administrateur, l'accès au dossier."""
    from . import messagerie5650 as mx
    from .membres5650 import access_card
    try:
        mx.ensure_schema(desk)
        row = desk.db.execute("SELECT f.id, p.last_read, p.follows FROM fils5650 f LEFT JOIN participants5650 p ON p.thread=f.id AND p.owner=? "
                              "WHERE f.kind='dossier' AND f.matter=?", (me, mid)).fetchone()
        count = total = 0
        if row:
            total = desk.db.execute('SELECT COUNT(*) FROM messages5650 WHERE thread=? AND deleted=0', (row['id'],)).fetchone()[0]
            if row['follows']:
                count = desk.db.execute('SELECT COUNT(*) FROM messages5650 WHERE thread=? AND id>? AND author<>? AND deleted=0',
                                        (row['id'], row['last_read'] or 0, me)).fetchone()[0]
        state = ('%d message(s), dont %d non lu(s)' % (total, count)) if count else ('%d message(s)' % total if total else 'aucun message pour l’instant')
        card = ('<section class="ax-card m50-matter"><h2>Discussion du dossier</h2><p>%s. Les membres qui voient ce dossier peuvent y écrire ; '
                'l’agent y dépose ses avis (document produit, réponse préparée, fiche mise à jour).</p><p><a class="ax-btn" href="%s">Ouvrir la discussion</a></p>'
                '</section>') % (e(state), e(prefix + '/messagerie?dossier=' + mid, quote=True))
    except Exception:   # noqa: BLE001 — la page du dossier ne dépend pas de la messagerie
        card = ''
    return card + access_card(desk, auth, prefix, mid, role)


# ------------------------------------------------------------------------------------------------ API
def api(env, desk, auth, prefix, name, args, method):
    from .web440 import check_post, json_out
    from . import messagerie5650 as mx
    from . import membres5650
    mx.ensure_schema(desk)
    route = name[len('m5650/'):].split('?', 1)[0]
    members = _members(desk, auth)
    me, role, my_name = _me(env, desk, auth, members)
    hidden = _hidden()
    if method == 'GET':
        if route == 'nonlus':
            return json_out({'non_lus': mx.unread_total(desk, me, hidden)})
        if route == 'fils':
            return json_out({'fils': mx.threads(desk, me, members, hidden)})
        if route == 'fil':
            return json_out({'fil': mx.thread_info(desk, me, args.get('id'), members, hidden),
                             'messages': mx.messages(desk, me, args.get('id'), hidden, int(args.get('apres') or 0))})
        if route == 'membres':
            return json_out({'membres': [{'owner': m['owner'], 'nom': m['name'] or m['owner'], 'role': m['role']}
                                         for o, m in sorted(members.items()) if o != me]})
        raise Stop('route_inconnue')
    data = check_post(env, auth)
    if route == 'direct':
        tid = mx.open_direct(desk, me, data.get('avec'), members)
        return json_out({'id': tid, 'goto': '?fil=%d' % tid, 'message': 'Conversation ouverte.'})
    if route == 'groupe':
        owners = [k[len('compte:'):] for k, v in data.items() if k.startswith('compte:') and v is True]
        tid = mx.create_group(desk, me, data.get('titre'), owners, members)
        return json_out({'id': tid, 'goto': '?fil=%d' % tid, 'message': 'Groupe créé.'})
    if route == 'groupe/membres':
        add = [str(x) for x in (data.get('ajouter') or [])][:MAX_LIST]
        rem = [str(x) for x in (data.get('retirer') or [])][:MAX_LIST]
        count = mx.change_group(desk, me, data.get('id'), add, rem, members, hidden)
        return json_out({'membres': count, 'message': 'Groupe mis à jour.'})
    if route == 'dossier':
        tid = mx.matter_thread(desk, str(data.get('matter') or ''), me, hidden)
        return json_out({'id': tid, 'goto': '?fil=%d' % tid})
    if route == 'suivre':
        mx.follow(desk, me, data.get('id'), bool(data.get('suivre')), hidden)
        return json_out({'message': 'Fil suivi.' if data.get('suivre') else 'Fil non suivi.'})
    if route == 'ecrire':
        out = mx.post(desk, me, my_name, role, data.get('id'), data.get('texte'), members, hidden, data.get('lien') or '', data.get('lien_libelle') or '')
        return json_out({**out, 'message': 'Message envoyé.'})
    if route == 'retirer':
        try:
            mid = int(data.get('message'))
        except (TypeError, ValueError):
            raise Stop('message_absent') from None
        mx.remove(desk, me, mid, hidden)
        return json_out({'message': 'Message retiré.'})
    if route == 'droits':
        if role != 'administrateur':
            raise Stop('role_insuffisant')
        owner = str(data.get('owner') or '')
        member = members.get(owner)
        if not member:
            raise Stop('compte_absent')
        values = {r: bool(data.get(r)) for r in membres5650.RIGHT_IDS}
        rights = membres5650.set_rights(desk.c['state_dir'], me, owner, member['role'], values)
        desk.audit('droits5650_modifies', {'compte': owner, 'par': me, 'droits': rights})
        return json_out({'droits': rights, 'message': 'Droits de « %s » enregistrés.' % (member['name'] or owner)})
    if route == 'reserve':
        if role != 'administrateur':
            raise Stop('role_insuffisant')
        mid = str(data.get('matter') or '')
        from .common import load_matters
        if not any(m['id'] == mid for m in load_matters(desk.c)):
            raise Stop('dossier_absent')
        owners = [k[len('compte:'):] for k, v in data.items() if k.startswith('compte:') and v is True and k[len('compte:'):] in members]
        saved = membres5650.set_reserve(desk.c['state_dir'], mid, owners)
        desk.audit('dossier5650_reserve', {'matter': mid, 'par': me, 'comptes': len(saved)})
        return json_out({'comptes': saved, 'message': ('Dossier réservé à %d compte(s) et aux administrateurs.' % len(saved)) if saved
                         else 'Dossier ouvert à tous les comptes du cabinet.'})
    raise Stop('route_inconnue')


MAX_LIST = 50
