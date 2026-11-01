"""5.6.88 : pages et API de sécurité : « Mon compte » (double authentification, jetons d'API), journal d'audit, ce que l'IA peut voir.

- ``/mon-compte`` : chaque personne active sa double authentification et gère ses jetons. Le QR code est écrit dans la page, jamais servi
  par une adresse : il contient le secret. Codes de secours et jetons ne sont affichés qu'une fois.
- ``/parametres/journal-audit`` (administrateur) : filtres, pagination, export CSV, état de la chaîne d'empreintes.
- ``/parametres/ia-visible`` (administrateur) : ``vueia5688``.
- Routes ``/api440/m5688s/*`` : ``admin/`` et ``journal`` sont aussi contrôlées ici : le droit « réglages » d'un compte ne les ouvre pas.
"""
import csv
from datetime import date, timedelta
import hashlib
from html import escape as e
import io
import json
import re
import threading
import time
from urllib.parse import urlencode

from .common import Stop

PER_PAGE = 50
EXPORT_LIMIT = 50000
ACTIVATION_OPEN = ('/mon-compte', '/api440/m5688s/2fa/debut', '/api440/m5688s/2fa/confirmer', '/api440/m5688s/2fa/annuler')
AUTHOR_SQL = ("CASE WHEN json_valid(payload) THEN COALESCE(json_extract(payload,'$.auteur'),json_extract(payload,'$.par'),"
              "json_extract(payload,'$.actor'),json_extract(payload,'$.owner')) END")
_chain = {}
_chain_lock = threading.Lock()


def allowed(role, route):
    """Son propre compte : tous les rôles ; journal, réglages et réinitialisations : administrateur seulement."""
    if role not in ('administrateur', 'avocat', 'assistant'):
        return False
    route = route.split('?', 1)[0]
    if route.startswith(('admin/', 'journal')):
        return role == 'administrateur'
    return route.startswith(('2fa/', 'jetons/'))


def activation_gate(method, prefix, path):
    """Double authentification exigée, pas encore activée : seule la page qui l'active répond. None : requête ouverte."""
    if path in ACTIVATION_OPEN or path.startswith('/static/'):
        return None
    if path.startswith(('/api', '/live/')) or method not in ('GET', 'HEAD'):
        return {'status': '403 Forbidden', 'kind': 'application/json; charset=utf-8',
                'body': json.dumps({'error': 'double_authentification_a_activer',
                                    'message': 'Le cabinet exige la double authentification : activez-la d’abord dans Mon compte.'}, ensure_ascii=False)}
    return {'status': '303 See Other', 'kind': 'text/html; charset=utf-8', 'body': '', 'headers': [('Location', (prefix or '') + '/mon-compte?exigee=1')]}


def _me(env):
    """La personne connectée. Le jeton de service n'a pas d'identité : il ne gère ni double authentification ni jetons."""
    from .web567 import actor
    if str(env.get('HTTP_AUTHORIZATION') or '').startswith('Bearer ') and not env.get('HTTP_X_AXIORHUB_USER'):
        raise Stop('role_insuffisant')
    return actor(env)


def _session_hash(env):
    from . import connexion5648 as cx
    token = cx.cookie_value(env) or cx.cookie_value(env, 'axiorhub_session')
    return hashlib.sha256(token.encode()).hexdigest() if token else ''


def _close_sessions(db, owner):
    from . import connexion5648 as cx
    from .comptes5642 import standalone
    if standalone():
        db.execute('DELETE FROM sessions WHERE user_id IN (SELECT id FROM users WHERE email=?)', (owner,))
        db.commit()
        return
    if owner == 'cabinet':
        cx.revoke(db, cx.ADMIN_ID)
        return
    row = db.execute('SELECT id FROM users WHERE ident=?', (owner,)).fetchone()
    if row:
        cx.revoke(db, row['id'])


def _revoke_nextcloud_keys(desk, auth, owner):
    """Les clés de l'application Nextcloud remises sans le code ne passent pas la nouvelle génération."""
    from . import integration5636
    from datetime import datetime, timezone
    integration5636.schema(desk)
    names = [owner] + ([str(auth.get('username') or '')] if owner == 'cabinet' else [])
    for name in names:
        desk.db.execute("UPDATE integration_keys5636 SET revoked=? WHERE axiorhub_user=? AND revoked=''", (datetime.now(timezone.utc).isoformat(), name))
    desk.db.commit()


def _api_2fa(env, desk, auth, route, data, owner, client):
    from . import jetons5688, totp5688
    from .web440 import json_out
    if not jetons5688.available(auth):
        raise Stop('comptes_nominatifs_requis')
    db = jetons5688.connect(desk.c['state_dir'])
    try:
        who = jetons5688.account(db, auth, owner)
        if not who:
            raise Stop('compte_absent')
        if route == '2fa/debut':
            totp5688.begin(db, owner, who['name'])
            return json_out({'message': 'Scannez le QR code, puis saisissez le premier code.'})
        if route == '2fa/annuler':
            totp5688.cancel(db, owner)
            return json_out({'message': 'Activation annulée.'})
        if route == '2fa/confirmer':
            codes, gen = totp5688.confirm_limited(db, owner, str(data.get('code') or ''), client)
            db.execute('UPDATE sessions SET mfa=? WHERE token_hash=?', (gen, _session_hash(env)))   # seule cette session reste ouverte
            db.commit()
            _revoke_nextcloud_keys(desk, auth, owner)
            desk.audit('double_auth_activee', {'compte': owner})
            return json_out({'message': 'Double authentification activée. Notez vos codes de secours : ils ne seront plus affichés.', 'noreload': True,
                             'text': '\n'.join(codes)})
        if not totp5688.active(db, owner):
            raise Stop('double_authentification_inactive')
        if route == '2fa/desactiver' and totp5688.required(db):
            raise Stop('double_authentification_exigee')
        if route not in ('2fa/desactiver', '2fa/codes'):
            raise Stop('route_inconnue')
        if not totp5688.attempt(db, owner, str(data.get('code') or ''), client):
            raise Stop('code_double_authentification_incorrect')
        if route == '2fa/desactiver':
            totp5688.disable(db, owner)
            desk.audit('double_auth_retiree', {'compte': owner})
            return json_out({'message': 'Double authentification retirée.'})
        codes = totp5688.renew_codes(db, owner)
        desk.audit('double_auth_codes_renouveles', {'compte': owner})
        return json_out({'message': 'Nouveaux codes de secours : les précédents ne valent plus.', 'noreload': True, 'text': '\n'.join(codes)})
    finally:
        db.close()


def _api_tokens(desk, auth, route, data, owner):
    from . import jetons5688
    from .web440 import json_out
    if not jetons5688.available(auth):
        raise Stop('comptes_nominatifs_requis')
    db = jetons5688.connect(desk.c['state_dir'])
    try:
        if route == 'jetons/creer':
            made = jetons5688.create(db, auth, owner, data.get('nom'), str(data.get('portee') or ''), data.get('jours'))
            desk.audit('jeton5688_cree', {'compte': owner, 'jeton': made['id'], 'portee': made['portee'], 'expire': made['expire']})
            return json_out({'message': 'Jeton créé : copiez-le maintenant, il ne sera plus affiché.', 'noreload': True, 'text': made['jeton']})
        if route == 'jetons/revoquer':
            jetons5688.revoke(db, str(data.get('id') or ''), owner)
            desk.audit('jeton5688_revoque', {'compte': owner, 'jeton': str(data.get('id') or '')[:16]})
            return json_out({'message': 'Jeton révoqué.'})
        raise Stop('route_inconnue')
    finally:
        db.close()


def _api_admin(desk, auth, route, data, me):
    from . import jetons5688, totp5688, vueia5688
    from .web440 import json_out
    if route == 'admin/vue-ia':
        saved = vueia5688.save(desk, data)
        desk.audit('vue_ia5688_modifiee', {'par': me, 'ouvert': [c for c, v in saved.items() if v]})
        return json_out({'message': 'Réglage enregistré : il s’applique dès la prochaine lecture.'})
    if not jetons5688.available(auth):
        raise Stop('comptes_nominatifs_requis')
    db = jetons5688.connect(desk.c['state_dir'])
    try:
        if route == 'admin/2fa/exiger':
            wanted = data.get('exiger') is True
            if wanted and not totp5688.active(db, me):   # jamais d'option qui fermerait la porte à celui qui l'active
                raise Stop('activez_d_abord_votre_double_authentification')
            totp5688.set_required(db, wanted)
            desk.audit('double_auth_exigence', {'par': me, 'exigee': wanted})
            return json_out({'message': 'Double authentification exigée pour tous les comptes.' if wanted else 'Double authentification facultative.'})
        if route == 'admin/2fa/reinitialiser':
            owner = str(data.get('compte') or '')
            if not owner or owner == me:
                raise Stop('droits_compte_invalide')
            state = totp5688.status(db, owner)
            if not state['actif'] and not state['en_attente']:
                raise Stop('double_authentification_inactive')
            totp5688.reset(db, owner)
            _close_sessions(db, owner)
            _revoke_nextcloud_keys(desk, auth, owner)
            desk.audit('double_auth_reinitialisee', {'compte': owner, 'par': me})
            return json_out({'message': 'Protection retirée pour « %s » : ses sessions, jetons et accès Claude sont fermés.' % owner})
        if route == 'admin/jetons/revoquer':
            jetons5688.revoke(db, str(data.get('id') or ''))
            desk.audit('jeton5688_revoque', {'par': me, 'jeton': str(data.get('id') or '')[:16]})
            return json_out({'message': 'Jeton révoqué.'})
        raise Stop('route_inconnue')
    finally:
        db.close()


def api(env, desk, auth, prefix, name, args, method):
    from .web440 import check_post
    from .garde5670 import client_ip
    route = name[len('m5688s/'):].split('?', 1)[0]
    me, role = _me(env)
    if route.startswith(('admin/', 'journal')) and role != 'administrateur':
        raise Stop('role_insuffisant')
    if route == 'journal/csv':
        if method != 'GET':
            raise Stop('methode_refusee')
        return _export(desk, args, me)
    if method != 'POST':
        raise Stop('methode_refusee')
    data = check_post(env, auth)
    if route.startswith('admin/'):
        return _api_admin(desk, auth, route, data, me)
    if route.startswith('2fa/'):
        return _api_2fa(env, desk, auth, route, data, me, client_ip(env))
    if route.startswith('jetons/'):
        return _api_tokens(desk, auth, route, data, me)
    raise Stop('route_inconnue')


# ------------------------------------------------------------------------------------------------ Mon compte
def _when(value):
    return e(str(value or '')[:16].replace('T', ' ')) or '—'


def _factor_section(db, owner, who, exigee):
    from . import totp5688
    st = totp5688.status(db, owner)
    notice = ('<p class="notice" role="status">Le cabinet exige la double authentification : activez-la pour continuer à utiliser AxiorHub.</p>'
              if exigee or (st['exigee'] and not st['actif']) else '')
    if st['actif']:
        retire = ('' if st['exigee'] else
                  '<form class="m5-form" data-api="m5688s/2fa/desactiver" data-reload autocomplete="off" data-confirm="Retirer la double authentification ?">'
                  '<label>Code actuel<input name="code" required maxlength="24" autocomplete="one-time-code"></label>'
                  '<button class="ax-btn danger" type="submit">Retirer la protection</button></form>')
        return ('<section class="ax-card" id="m88-2fa"><h2>Double authentification</h2>%s<p>Active depuis le %s. Codes de secours restants : '
                '<strong>%d</strong>.</p><form class="m5-form" data-api="m5688s/2fa/codes" data-result="#m88-codes" autocomplete="off">'
                '<label>Code actuel<input name="code" required maxlength="24" autocomplete="one-time-code"></label>'
                '<button class="ax-btn ghost" type="submit">Générer de nouveaux codes de secours</button></form>'
                '<label class="m88-once">Nouveaux codes (affichés une seule fois)<textarea id="m88-codes" rows="5" readonly></textarea></label>%s</section>') % (
            notice, _when(st['active_le']), st['codes_restants'], retire)
    if st['en_attente']:
        pack = totp5688.enrolment(db, owner, who['name'])
        return ('<section class="ax-card" id="m88-2fa"><h2>Double authentification</h2>%s<ol class="m88-steps">'
                '<li>Installez une application d’authentification sur votre téléphone, puis scannez ce QR code.</li></ol>'
                '<div class="m88-qr" role="img" aria-label="QR code à scanner avec votre application d’authentification">%s</div>'
                '<p class="ax-muted">Sans appareil photo, saisissez cette clé dans l’application : <code>%s</code></p>'
                '<form class="m5-form" data-api="m5688s/2fa/confirmer" data-result="#m88-codes" autocomplete="off">'
                '<label>Premier code affiché par l’application<input name="code" required maxlength="8" inputmode="numeric" autocomplete="one-time-code"></label>'
                '<button class="ax-btn" type="submit">Confirmer</button></form>'
                '<label class="m88-once">Codes de secours (affichés une seule fois, à conserver hors du téléphone)<textarea id="m88-codes" rows="5" readonly></textarea></label>'
                '<p><a class="ax-btn ghost" href="?fait=1">J’ai noté mes codes</a></p>'
                '<form class="m5-form" data-api="m5688s/2fa/annuler" data-reload><button class="ax-btn ghost" type="submit">Annuler l’activation</button></form>'
                '</section>') % (notice, pack['svg'], e(pack['secret']))
    return ('<section class="ax-card" id="m88-2fa"><h2>Double authentification</h2>%s<p class="ax-muted">Après le mot de passe, AxiorHub demande un code à six '
            'chiffres renouvelé toutes les 30 secondes par une application de votre téléphone. Un mot de passe volé ne suffit plus.</p>'
            '<form class="m5-form" data-api="m5688s/2fa/debut" data-reload><button class="ax-btn" type="submit">Activer la double authentification</button></form>'
            '</section>') % notice


def _token_section(db, auth, owner):
    from . import jetons5688, totp5688
    who, gen = jetons5688.account(db, auth, owner), totp5688.generation(db, owner)
    rows = ''.join('<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>' % (
        e(t['nom']), e(jetons5688.SCOPES.get(t['portee'], t['portee'])), _when(t['expire']), _when(t['dernier_usage']), e(jetons5688.state_of(t, who, gen)),
        '' if t['revoque'] else ('<form class="m5-form" data-api="m5688s/jetons/revoquer" data-reload data-confirm="Révoquer ce jeton ?"><input type="hidden" name="id" '
                                 'value="%s"><button class="ax-btn ghost" type="submit">Révoquer</button></form>' % e(t['id'], quote=True)))
        for t in jetons5688.listing(db, owner))
    durations = ''.join('<option value="%d"%s>%d jours</option>' % (d, ' selected' if d == 90 else '', d) for d in jetons5688.DURATIONS)
    scopes = ''.join('<option value="%s">%s</option>' % (k, e(v)) for k, v in jetons5688.SCOPES.items())
    table = ('<div class="ax-table-wrap"><table><thead><tr><th>Nom</th><th>Portée</th><th>Expire le</th><th>Dernier usage</th><th>État</th><th></th></tr></thead>'
             '<tbody>%s</tbody></table></div>' % rows) if rows else '<p>Aucun jeton pour l’instant.</p>'
    return ('<section class="ax-card" id="m88-jetons"><h2>Jetons d’API</h2><p class="ax-muted">Un jeton donne à un script l’accès à l’API en votre nom, avec vos droits et '
            'vos dossiers. Il n’est affiché qu’une fois, expire toujours, et cesse de fonctionner si votre mot de passe change.</p>%s'
            '<details class="m88-new"><summary>Créer un jeton</summary><form class="m5-form" data-api="m5688s/jetons/creer" data-result="#m88-jeton" autocomplete="off">'
            '<label>Nom (à quoi il sert)<input name="nom" required maxlength="80"></label><label>Portée<select name="portee">%s</select></label>'
            '<label>Durée<select name="jours">%s</select></label><button class="ax-btn" type="submit">Créer le jeton</button></form>'
            '<label class="m88-once">Jeton (affiché une seule fois)<textarea id="m88-jeton" rows="2" readonly></textarea></label></details></section>') % (
        table, scopes, durations)


def compte_page(desk, auth, prefix, env, args):
    from . import jetons5688, membres5650, totp5688
    owner, role = _me(env)
    if not jetons5688.available(auth):
        return ('<h1>Mon compte</h1><section class="ax-card"><p>Cette installation n’a pas de comptes nominatifs : l’accès passe par ce poste ou par Nextcloud, qui '
                'gère sa propre double authentification.</p></section>')
    db = jetons5688.connect(desk.c['state_dir'])
    try:
        who = jetons5688.account(db, auth, owner) or {'name': owner, 'role': role}
        factor = _factor_section(db, owner, who, bool(args.get('exigee')))
        tokens = _token_section(db, auth, owner)
    finally:
        db.close()
    rules = ''.join('<li><strong>%s</strong> : %s</li>' % (e(a), e(b)) for a, b in totp5688.ACCES)
    delegated = ''
    if role != 'administrateur' and membres5650.can_manage_accounts(desk.c['state_dir'], owner, role):
        from . import connexion5648 as cx
        delegated = cx.settings_html(desk.c['state_dir'], auth, delegated=True) if cx.enabled(auth) else (
            '<section class="ax-card"><h2>Comptes du cabinet</h2><p><a class="ax-btn" href="/comptes">Gérer les comptes</a></p></section>')
    return ('<h1>Mon compte</h1><p class="ax-muted">%s</p>%s%s<section class="ax-card"><h2>Ce que la double authentification couvre</h2><ul class="m88-acces">%s</ul>'
            '</section>%s') % (e(who['name']), factor, tokens, rules, delegated)


# ------------------------------------------------------------------------------------------------ administrateur : Paramètres › Cabinet
def admin_block(desk, auth, prefix):
    """Option du cabinet, état de chaque compte, réinitialisation et jetons, pour l'administrateur."""
    from . import jetons5688, membres5650, totp5688
    if not jetons5688.available(auth):
        return ''
    db = jetons5688.connect(desk.c['state_dir'])
    try:
        members = [m for m in membres5650.directory(desk, auth).values() if m.get('source') == 'compte']
        lines = []
        for m in sorted(members, key=lambda x: (x['name'] or '').lower()):
            st = totp5688.status(db, m['owner'])
            reset = ('<form class="m5-form" data-api="m5688s/admin/2fa/reinitialiser" data-reload data-confirm="Retirer la protection de ce compte ? Ses sessions, jetons et accès '
                     'Claude seront fermés."><input type="hidden" name="compte" value="%s"><button class="ax-btn ghost" type="submit">Réinitialiser</button></form>'
                     % e(m['owner'], quote=True)) if st['actif'] or st['en_attente'] else ''
            lines.append('<tr><td>%s<br><small>%s</small></td><td>%s</td><td>%d</td><td>%s</td></tr>' % (
                e(m['name'] or m['owner']), e(m['owner']), 'Active' if st['actif'] else ('Activation en cours' if st['en_attente'] else 'Non activée'),
                sum(1 for t in jetons5688.listing(db, m['owner']) if not t['revoque'] and t['expire'] > jetons5688._iso()), reset))
        required = totp5688.required(db)
    finally:
        db.close()
    return ('<section class="ax-card" id="m88-admin"><h2>Double authentification et jetons</h2><p class="ax-muted">Facultative par défaut. Si le cabinet l’exige, chaque compte doit '
            'l’activer à sa prochaine connexion. Vos propres réglages se font dans <a href="%s/mon-compte">Mon compte</a>.</p>'
            '<form class="m5-form" data-api="m5688s/admin/2fa/exiger" data-reload><label class="m50-check"><input type="checkbox" name="exiger"%s> Exiger la double '
            'authentification pour tous les comptes</label><button class="ax-btn ghost" type="submit">Enregistrer</button></form>'
            '<div class="ax-table-wrap"><table><thead><tr><th>Compte</th><th>Protection</th><th>Jetons actifs</th><th></th></tr></thead><tbody>%s</tbody></table></div>'
            '<p class="ax-muted">Sur le serveur, sans accès à AxiorHub : <code>sudo python3 /opt/axiorhub-mail-agent/current/reinitialiser-acces.py --2fa identifiant</code>.</p>'
            '</section>') % (e(prefix, quote=True), ' checked' if required else '', ''.join(lines))


# ------------------------------------------------------------------------------------------------ journal d'audit
def _day(value):
    value = str(value or '').strip()
    if not value:
        return ''
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError:
        raise Stop('filtre_journal_invalide') from None


def _where(args):
    clauses, params = [], []
    start, end = _day(args.get('du')), _day(args.get('au'))
    if start:
        clauses.append('at>=?')
        params.append(start)
    if end:
        clauses.append('at<?')
        params.append((date.fromisoformat(end) + timedelta(days=1)).isoformat())
    if args.get('action'):
        clauses.append('action=?')
        params.append(str(args['action'])[:120])
    if args.get('auteur'):
        clauses.append('(%s)=?' % AUTHOR_SQL)
        params.append(str(args['auteur'])[:120])
    if args.get('q'):
        like = '%' + re.sub(r'([\\%_])', r'\\\1', str(args['q'])[:200]) + '%'
        clauses.append("(action LIKE ? ESCAPE '\\' OR payload LIKE ? ESCAPE '\\')")
        params += [like, like]
    return (' WHERE ' + ' AND '.join(clauses)) if clauses else '', params


def journal(desk, args):
    """Lignes du journal filtrées, de la plus récente à la plus ancienne, avec le nombre total pour la pagination."""
    from .cabinet_pilot import ensure_schema
    ensure_schema(desk)
    where, params = _where(args)
    total = desk.db.execute('SELECT COUNT(*) FROM audit_chain_v290' + where, params).fetchone()[0]
    try:
        page = max(1, int(args.get('page') or 1))
    except ValueError:
        page = 1
    rows = desk.db.execute('SELECT seq,at,action,payload,event_hash,%s AS auteur FROM audit_chain_v290%s ORDER BY seq DESC LIMIT ? OFFSET ?' % (AUTHOR_SQL, where),
                           params + [PER_PAGE, (page - 1) * PER_PAGE]).fetchall()
    return {'rows': [dict(r) for r in rows], 'total': total, 'page': page, 'pages': max(1, (total + PER_PAGE - 1) // PER_PAGE)}


def chain_state(desk):
    """Vérification complète de la chaîne, gardée une minute tant qu'aucune ligne n'est ajoutée (elle relit tout le journal)."""
    from .cabinet_pilot import audit_status, ensure_schema
    ensure_schema(desk)
    key = str(desk.c['state_dir'])
    last = desk.db.execute('SELECT COALESCE(MAX(seq),0) FROM audit_chain_v290').fetchone()[0]
    with _chain_lock:
        hit = _chain.get(key)
        if hit and hit[0] == last and hit[1] > time.time():
            return hit[2]
    out = audit_status(desk, 1)
    state = {'valid': out['valid'], 'broken_at': out['broken_at'], 'count': out['count'], 'head': out['head']}
    with _chain_lock:
        _chain[key] = (last, time.time() + 60, state)
    return state


def _cell(value):
    """Cellule CSV sans formule : un tableur n'exécute jamais une valeur écrite dans le journal."""
    text = str(value if value is not None else '')
    return "'" + text if text[:1] in ('=', '+', '-', '@', '\t', '\r') else text


def _export(desk, args, me):
    from .cabinet_pilot import ensure_schema
    from .common import set_request_mode
    set_request_mode('write')
    ensure_schema(desk)
    where, params = _where(args)
    out = io.StringIO()
    out.write('﻿')
    writer = csv.writer(out, delimiter=';', lineterminator='\r\n')
    writer.writerow(('Numéro', 'Date', 'Action', 'Auteur', 'Contenu', 'Empreinte'))
    rows = desk.db.execute('SELECT seq,at,action,payload,event_hash,%s AS auteur FROM audit_chain_v290%s ORDER BY seq DESC LIMIT ?' % (AUTHOR_SQL, where),
                           params + [EXPORT_LIMIT]).fetchall()
    for r in rows:
        writer.writerow((r['seq'], _cell(r['at']), _cell(r['action']), _cell(r['auteur']), _cell(r['payload']), r['event_hash']))
    desk.audit('journal_audit_exporte', {'par': me, 'lignes': len(rows), 'filtres': {k: str(args[k])[:80] for k in ('du', 'au', 'action', 'auteur', 'q') if args.get(k)}})
    return {'status': '200 OK', 'kind': 'text/csv; charset=utf-8', 'body': out.getvalue(),
            'headers': [('Content-Disposition', 'attachment; filename="journal-audit.csv"'), ('Cache-Control', 'no-store')]}


def _select(name, label, values, current):
    options = '<option value="">Tous</option>' + ''.join('<option value="%s"%s>%s</option>' % (e(v, quote=True), ' selected' if v == current else '', e(v)) for v in values)
    return '<label>%s<select name="%s">%s</select></label>' % (e(label), name, options)


def journal_page(desk, auth, prefix, env, args):
    _, role = _me(env)
    if role != 'administrateur':
        raise Stop('role_insuffisant')
    try:
        found = journal(desk, args)
        error = ''
    except Stop:
        found, error = journal(desk, {}), '<p class="notice" role="alert">Filtre invalide : vérifiez les dates.</p>'
        args = {}
    state = chain_state(desk)
    verdict = ('<p class="m88-ok" role="status"><strong>Chaîne intacte</strong> : %d événement(s), aucune ligne modifiée ni retirée.</p>' % state['count'] if state['valid'] else
               '<p class="m88-bad" role="alert"><strong>Chaîne rompue à l’événement n° %s</strong> : une ligne a été modifiée ou retirée. Conservez une copie de la base et '
               'cherchez la cause.</p>' % e(str(state['broken_at'])))
    actions = [r[0] for r in desk.db.execute('SELECT DISTINCT action FROM audit_chain_v290 ORDER BY action LIMIT 500')]
    authors = [r[0] for r in desk.db.execute('SELECT DISTINCT %s FROM audit_chain_v290 ORDER BY 1 LIMIT 500' % AUTHOR_SQL) if r[0]]
    kept = {k: str(args[k]) for k in ('du', 'au', 'action', 'auteur', 'q') if args.get(k)}
    rows = ''.join('<tr><td>%d</td><td>%s</td><td>%s</td><td>%s</td><td class="m88-payload">%s</td></tr>' % (
        r['seq'], _when(r['at']), e(r['action'].replace('_', ' ')), e(str(r['auteur'] or 'système')), e(str(r['payload'])[:300])) for r in found['rows'])
    nav = []
    if found['page'] > 1:
        nav.append('<a class="ax-btn ghost" href="?%s">Plus récents</a>' % e(urlencode({**kept, 'page': found['page'] - 1}), quote=True))
    if found['page'] < found['pages']:
        nav.append('<a class="ax-btn ghost" href="?%s">Plus anciens</a>' % e(urlencode({**kept, 'page': found['page'] + 1}), quote=True))
    return ('<h1>Journal d’audit</h1><p class="ax-muted">Chaque action sensible y laisse une trace chaînée : modifier ou retirer une ligne rompt la chaîne.</p>%s%s'
            '<section class="ax-card"><h2>Filtres</h2><form method="get" class="m88-filters"><label>Du<input type="date" name="du" value="%s"></label>'
            '<label>Au<input type="date" name="au" value="%s"></label>%s%s<label>Recherche<input type="search" name="q" value="%s" maxlength="200"></label>'
            '<button class="ax-btn" type="submit">Filtrer</button> <a class="ax-btn ghost" href="%s/api440/m5688s/journal/csv?%s">Exporter (CSV)</a></form></section>'
            '<section class="ax-card"><h2>%d événement(s)</h2><div class="ax-table-wrap"><table class="m88-journal"><thead><tr><th>N°</th><th>Date</th><th>Action</th><th>Auteur</th>'
            '<th>Contenu</th></tr></thead><tbody>%s</tbody></table></div><nav class="m88-pages" aria-label="Pages du journal">%s<span>Page %d sur %d</span></nav></section>') % (
        verdict, error, e(kept.get('du', ''), quote=True), e(kept.get('au', ''), quote=True), _select('action', 'Action', actions, kept.get('action')),
        _select('auteur', 'Auteur', authors, kept.get('auteur')), e(kept.get('q', ''), quote=True), e(prefix, quote=True), e(urlencode(kept), quote=True), found['total'],
        rows or '<tr><td colspan="5">Aucun événement pour ces filtres.</td></tr>', ''.join(nav), found['page'], found['pages'])


def vue_ia_page(desk, auth, prefix, env, args):
    from . import vueia5688
    _, role = _me(env)
    if role != 'administrateur':
        raise Stop('role_insuffisant')
    return vueia5688.settings_html(desk, prefix)
