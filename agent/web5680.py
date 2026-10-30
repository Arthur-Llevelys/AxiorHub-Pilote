"""5.6.80 : ``POST /api440/m5680/passages`` : passages trouvés par l'index plein texte, sans modèle ni réseau, affichés pendant que l'IA répond
(et en repli si elle tarde). Mêmes droits que la lecture du dossier : dossier réservé refusé, résultats d'un dossier réservé écartés."""
from .common import Stop


def api(env, desk, auth, prefix, name, args, method):
    from .web440 import json_out
    from .web567 import actor
    from .web5634 import _post
    from .common import load_matters
    from .membres5650 import viewer_hidden
    from . import rapide5680
    owner, role = actor(env)
    route = name[len('m5680/'):].split('?', 1)[0]
    if route != 'passages':
        raise Stop('operation_absente')
    if method != 'POST':
        raise Stop('methode_refusee')
    if role not in ('administrateur', 'avocat', 'assistant'):
        raise Stop('role_insuffisant')
    data = _post(env, auth, 20_000)
    matter = str(data.get('matter') or '')
    if matter:
        if matter in (viewer_hidden() or frozenset()):
            raise Stop('dossier_reserve')
        if not any(str(m['id']) == matter for m in load_matters(desk.c)):
            raise Stop('dossier_absent')
    question = str(data.get('question') or '').strip()
    if not 3 <= len(question) <= 12000:
        raise Stop('question_requise_12000_caracteres_maximum')
    return json_out({'passages': rapide5680.passages(desk.c, matter, question, 4)})
