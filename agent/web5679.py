"""5.6.79 : routes de la page Dossier refondue.

- ``GET /dossier/apercu?matter=…&path=…`` : description JSON de l'aperçu d'un document (volet de droite) ;
- ``GET /dossier/fichier?matter=…&path=…[&dl=1]`` : PDF ou image affichés dans ce volet, ou fichier téléchargé ;
- ``POST /api440/m5679/strategie`` : question stratégique sur le dossier (travail « analyze_strategy », avocat ou administrateur) ;
- ``POST /api440/m5679/courrier`` : nouveau courrier sur le modèle Word du cabinet (travail « courrier5679 », avocat ou administrateur) ;
- ``POST /api440/m5679/correspondant`` : enregistrer une adresse repérée comme correspondant du dossier (droit « Valider »).

Le contrôle central (membres5650.gate) refuse déjà un dossier réservé ; chaque route revérifie le dossier et l'appartenance du fichier.
"""
from .common import Stop

LAWYER_ROUTES = ('strategie', 'courrier')
VALIDATE_ROUTES = ('correspondant',)


def route(cfg, auth, prefix, path, args):
    from .desk import Desk
    from . import apercu5679
    from .web440 import json_out
    desk = Desk(cfg)
    try:
        mid, file_path = str(args.get('matter') or ''), str(args.get('path') or '')
        try:
            if path == '/dossier/apercu':
                return json_out(apercu5679.preview(desk, prefix, mid, file_path))
            return apercu5679.file_response(desk, mid, file_path, download=str(args.get('dl') or '') == '1')
        except Stop as ex:
            code = str(ex).split(':')[0]
            from .web import REASONS
            from .erreurs5634 import explain
            status = '403 Forbidden' if code in ('dossier_reserve', 'fichier_hors_dossier') else ('404 Not Found' if 'absent' in code or 'introuvable' in code
                                                                                                 else '400 Bad Request')
            return json_out({'error': code, 'message': REASONS.get(code) or explain(code, getattr(ex, 'detail', None))}, status)
    finally:
        try:
            desk.db.close()
        except Exception:   # noqa: BLE001
            pass


def api(env, desk, auth, prefix, name, args, method):
    from .web440 import json_out
    from .web567 import actor
    from .web5634 import _post
    from . import modele5651
    owner, role = actor(env)
    route_name = name[len('m5679/'):].split('?', 1)[0]
    if method != 'POST':
        raise Stop('methode_refusee')
    if route_name not in LAWYER_ROUTES + VALIDATE_ROUTES:
        raise Stop('operation_absente')
    if role not in ('administrateur', 'avocat'):
        raise Stop('role_insuffisant')
    data = _post(env, auth, 50_000)
    if route_name in VALIDATE_ROUTES:
        from .membres5650 import effective_rights
        try:
            may = effective_rights(desk.c['state_dir'], owner, role)['valider']
        except Stop:
            may = role == 'administrateur'
        if not may:
            raise Stop('role_insuffisant')
    m = modele5651._matter(desk, str(data.get('matter') or ''))
    from .membres5650 import viewer_hidden
    if m['id'] in (viewer_hidden() or frozenset()):
        raise Stop('dossier_reserve')
    mid = m['id']
    if route_name == 'strategie':
        question = ' '.join(str(data.get('question') or '').split())
        if not 10 <= len(question) <= 3000:
            raise Stop('question_strategique_invalide')
        pending = desk.db.execute("SELECT 1 FROM jobs WHERE kind='analyze_strategy' AND status IN ('pending','running') AND args LIKE ?",
                                  ('%"matter": "' + mid.replace('%', '') + '"%',)).fetchone()
        if pending:
            raise Stop('analyse_strategique_deja_en_cours')
        job = desk.enqueue('analyze_strategy', {'matter': mid, 'objective': question})
        return json_out({'job': job, 'message': 'Analyse lancée : le résultat s’affichera dans cet onglet.'})
    if route_name == 'courrier':
        from .courrier5679 import request
        ident = request(desk, owner, mid, data)
        return json_out({'id': ident, 'message': 'Rédaction lancée : le brouillon Word apparaîtra dans « Courriers préparés ».'})
    email = str(data.get('email') or '').strip().lower()
    desk.associate({'matter': mid, 'email': email, 'role': str(data.get('role') or '')})
    return json_out({'ok': True, 'message': 'Correspondant enregistré pour ce dossier.'})
