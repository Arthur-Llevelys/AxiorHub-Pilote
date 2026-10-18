"""5.6.61 : routes ``/api440/m5661/maj/demander`` (mise à jour signée vers la dernière version publiée) et ``maj/automatique``
(installation automatique la nuit, ou sur clic) — administrateur seulement."""
from .common import Stop

WRITE = ('maj/demander', 'maj/automatique', 'maj/verifier')   # 5.6.62 : « Vérifier maintenant »
CATEGORIES = {'maj/demander': 'reglages', 'maj/automatique': 'reglages', 'maj/verifier': 'reglages'}
WORK = ('file/missions',)   # 5.6.61 : Relancer / Écarter depuis la file (mêmes droits que m567/mission/control)


def allowed(role, route):
    route = route.split('?', 1)[0]
    if route in WORK:
        return role in ('administrateur', 'avocat', 'assistant')
    return role == 'administrateur' and route in WRITE


def api(env, desk, auth, prefix, name, args, method):
    from .web440 import json_out
    from .web567 import actor
    from .web5634 import _post
    owner, role = actor(env)
    route = name[len('m5661/'):].split('?', 1)[0]
    if not allowed(role, route):
        raise Stop('role_insuffisant')
    if method != 'POST':
        raise Stop('methode_refusee')
    data = _post(env, auth, 2_000)
    if route == 'file/missions':
        from . import missions567
        action = str(data.get('action') or '')
        ids = [x for x in str(data.get('ids') or '').split(',') if x][:20]
        if action not in ('resume', 'cancel') or not ids:
            raise Stop('demande_invalide')
        done, errors = 0, []
        for mission_id in ids:
            try:
                missions567.control(desk, {'id': mission_id, 'action': action}, owner, role == 'administrateur')
                done += 1
            except Stop as ex:
                errors.append(str(ex))
        if not done:
            raise Stop(errors[0] if errors else 'mission_introuvable')
        return json_out({'ok': True, 'message': ('%d travail relancé.' if action == 'resume' else '%d travail écarté.') % done
                         if done == 1 else ('%d travaux relancés.' if action == 'resume' else '%d travaux écartés.') % done})
    from . import maj5661
    if route == 'maj/verifier':   # 5.6.62 : lecture immédiate du flux public des versions (sinon au plus toutes les 6 h)
        state = maj5661.check(desk, force=True)
        new = maj5661.available(desk)
        return json_out({'ok': True, 'message': ('Nouvelle version disponible : %s.' % new) if new else (
            'Pas d’accès à GitHub pour l’instant.' if state.get('error') else 'Vous avez la dernière version publiée.')})
    if route == 'maj/automatique':
        return json_out(maj5661.set_automatic(desk, str(data.get('actif') or '') in ('on', '1', 'true')))
    return json_out(maj5661.request(desk, owner, str(data.get('version') or '')))
