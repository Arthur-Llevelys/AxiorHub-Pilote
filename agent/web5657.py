"""5.6.57 : routes ``/api440/m5657/...`` des parcours livrés par la 5.6.57.

- ``documents/auto`` : autorisation (une fois, pour le cabinet) de créer automatiquement les nouvelles versions de brouillon des projets
  documentaires dans le sous-dossier de brouillons du dossier (droit « réglages ») ;
- ``relecture/style`` : accepter en une fois les modifications de pure formulation d'une relecture Word (les faits, demandes et
  fondements restent décidés un par un) ;
- les autres routes sont ajoutées par les modules des parcours (contacts, préparations, demandes de pièces, suivi client, diligences).

Contrôles : rôle (``allowed``), droit réglable du compte (``CATEGORIES`` lu par ``membres5650.category``), dossier réel de l'objet.
"""
import json

from .common import Stop

READ = ('reprise',)
SETTINGS = ('documents/auto', 'contacts/reglages')
WRITE = ('relecture/style', 'contacts/collecter', 'preparation/creer', 'preparation/note', 'pieces/preparer', 'pieces/rapprocher', 'pieces/statut',
         'pieces/deposer', 'suivi/preparer', 'suivi/decider', 'suivi/deposer', 'diligence/confirmer', 'diligence/ecarter', 'contacts/classer')
# droit réglable concerné par chaque écriture (membres5650.category)
CATEGORIES = {'documents/auto': 'reglages', 'contacts/reglages': 'reglages', 'relecture/style': 'valider', 'diligence/confirmer': 'facturation',
              'diligence/ecarter': 'facturation', 'pieces/deposer': 'deposer', 'suivi/deposer': 'deposer'}


def allowed(role, route):
    route = route.split('?', 1)[0]
    if role not in ('administrateur', 'avocat', 'assistant'):
        return False
    if route in READ:
        return True
    if route in SETTINGS:
        return role in ('administrateur', 'avocat')
    return route in WRITE


def api(env, desk, auth, prefix, name, args, method):
    from .web440 import json_out
    from .web567 import actor
    from .web5634 import _post
    owner, role = actor(env)
    route = name[len('m5657/'):].split('?', 1)[0]
    if not allowed(role, route):
        raise Stop('role_insuffisant')
    if method != 'POST':
        if route == 'reprise':
            from . import reprise5657
            return json_out({'elements': reprise5657.items(desk, owner, prefix)})
        raise Stop('methode_refusee')
    data = _post(env, auth, 400_000)
    _require(desk, owner, role, CATEGORIES.get(route))
    out = post(desk, owner, role, route, data, prefix)
    return json_out(out)


def _require(desk, owner, role, right):
    if not right or role == 'administrateur':
        return
    from .membres5650 import effective_rights
    try:
        rights = effective_rights(desk.c['state_dir'], owner, role)
    except Stop:
        raise Stop('controle_acces_indisponible') from None
    if not rights.get(right):
        raise Stop('role_insuffisant')


def _matter(desk, mid):
    from .membres5650 import viewer_hidden
    from .common import load_matters
    if str(mid) in (viewer_hidden() or frozenset()):
        raise Stop('dossier_reserve')
    m = next((x for x in load_matters(desk.c) if str(x['id']) == str(mid)), None)
    if not m:
        raise Stop('dossier_absent')
    return m


def post(desk, owner, role, route, data, prefix=''):
    if route == 'documents/auto':
        on = data.get('actif') in (True, 'true', 'on', '1', 'oui')
        desk.setting('documents5657:brouillons_auto', on)
        desk.audit('documents5657_brouillons_auto', {'actif': on, 'par': owner})
        return {'message': 'Nouvelles versions de brouillon créées automatiquement.' if on else 'Création des fichiers soumise à votre confirmation.'}
    if route == 'relecture/style':
        from . import relecture5651
        mid = str(data.get('matter') or '')
        _matter(desk, mid)
        s = relecture5651.session(desk, mid, str(data.get('session') or ''))
        done = 0
        for p in s['proposals']:
            if p['nature'] == 'style' and p['state'] == 'proposee' and not p['a_recontroler']:
                relecture5651.decide(desk, mid, p['id'], owner, 'accepter', p['version'])
                done += 1
        return {'acceptees': done, 'message': '%d modification(s) de formulation acceptée(s). Les autres restent à décider une par une.' % done}
    from . import parcours5657
    return parcours5657.post(desk, owner, role, route, data, prefix)


def dumps(value):
    return json.dumps(value, ensure_ascii=False)
