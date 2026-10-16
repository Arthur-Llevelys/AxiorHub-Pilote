"""5.6.32 : dossiers de travail ajoutés par l'utilisateur dans chaque dossier client (réglages de « Mon style »).

Les quatre sous-dossiers historiques (PROCEDURE, CORRESPONDANCES, PROJETS, LIVRABLES) restent réglés par docrequest520. On y ajoute une
liste libre de dossiers de travail, chacun avec un USAGE :
- nos_pieces       pièces que nous produisons (« NOS PIECES ») ;
- pieces_adverses  pièces communiquées par une partie adverse (« PIECES ADVERSES - {{partie_adverse01}} ») ;
- ecrits           écrits du cabinet : appris pour le style, comme PROCEDURE ou CORRESPONDANCES ;
- travail          autre dossier de travail : lu pour l'analyse du dossier seulement.

Variables : {{partie_adverse01}} à {{partie_adverse09}} (parties adverses du dossier, dans l'ordre de saisie : registre des conflits
puis fiche du dossier) et {{client}}. Un nom dont la variable n'est pas renseignée pour un dossier n'est pas utilisé dans ce dossier ;
un dossier existant de même forme (« PIECES ADVERSES - … ») est tout de même reconnu, quel que soit le nom écrit après le tiret.
Les pièces (nos pièces, pièces adverses) ne servent jamais à apprendre le style : ce ne sont pas des écrits de l'avocat.
"""
import json
import re
import unicodedata
from pathlib import PurePosixPath

from .common import Stop, clean_path, under

SETTING = 'docreq5632:extra_folders'
USAGES = {'nos_pieces': 'Nos pièces', 'pieces_adverses': 'Pièces adverses', 'ecrits': 'Écrits du cabinet (appris pour le style)',
          'travail': 'Autre dossier de travail',
          'facturation': 'Facturation (factures, avoirs, devis, conventions d’honoraires) — jamais des pièces'}   # 5.6.53
DEFAULTS = ({'pattern': 'NOS PIECES', 'usage': 'nos_pieces'},
            {'pattern': 'PIECES ADVERSES - {{partie_adverse01}}', 'usage': 'pieces_adverses'},
            {'pattern': 'PIECES ADVERSES - {{partie_adverse02}}', 'usage': 'pieces_adverses'},
            {'pattern': 'PIECES ADVERSES - {{partie_adverse03}}', 'usage': 'pieces_adverses'},
            {'pattern': 'FACTURATION', 'usage': 'facturation'})   # 5.6.53 : demande de l'avocat
MAX_FOLDERS = 20
VAR = re.compile(r'\{\{\s*(partie_adverse0[1-9]|client)\s*\}\}')
NAME_OK = re.compile(r"[\w .'’()&\-]{1,80}")


def _fold(text):
    text = unicodedata.normalize('NFKD', str(text or '')).encode('ascii', 'ignore').decode().lower()
    return re.sub(r'\s+', ' ', text).strip()


def validate_pattern(pattern):
    """Nom de dossier (une seule partie de chemin) avec, au plus, des variables connues."""
    pattern = re.sub(r'\s+', ' ', str(pattern or '')).strip().strip('/')
    if not pattern:
        return ''
    bare = VAR.sub('X', pattern)
    if '{' in bare or '}' in bare:
        raise Stop('variable_dossier_inconnue')
    if '/' in bare or '\\' in bare or bare in ('.', '..') or not NAME_OK.fullmatch(bare):
        raise Stop('nom_sous_dossier_invalide')
    return pattern


def configured(desk):
    raw = desk.settings(SETTING, None)
    if raw is None:
        return [dict(x) for x in DEFAULTS]
    out = []
    for item in raw if isinstance(raw, list) else []:
        if isinstance(item, dict) and item.get('pattern') and item.get('usage') in USAGES:
            out.append({'pattern': str(item['pattern'])[:120], 'usage': item['usage']})
    if not any(x['usage'] == 'facturation' for x in out) and not desk.settings('dossiers5653:facturation_retiree', False):
        out.append({'pattern': 'FACTURATION', 'usage': 'facturation'})   # 5.6.53 : ajouté aux réglages existants (retirable)
    return out


def save(desk, data):
    """Lignes extra_pattern_N / extra_usage_N du formulaire ; une ligne vide est retirée. Les doublons sont refusés."""
    rows, seen = [], set()
    keys = sorted({int(m.group(1)) for k in data for m in [re.fullmatch(r'extra_pattern_(\d{1,2})', str(k))] if m})
    for i in keys:
        pattern = validate_pattern(data.get('extra_pattern_%d' % i))
        if not pattern:
            continue
        usage = str(data.get('extra_usage_%d' % i) or 'travail')
        if usage not in USAGES:
            raise Stop('usage_dossier_invalide')
        key = _fold(pattern)
        if key in seen:
            raise Stop('dossier_de_travail_en_double')
        seen.add(key)
        rows.append({'pattern': pattern, 'usage': usage})
    if len(rows) > MAX_FOLDERS:
        raise Stop('trop_de_dossiers_de_travail')
    desk.setting('dossiers5653:facturation_retiree', not any(r['usage'] == 'facturation' for r in rows))
    desk.setting(SETTING, rows)
    return rows


def _clean_name(name):
    """Nom de partie utilisable dans un nom de dossier : pas de séparateur de chemin ni de caractère réservé."""
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]+', ' ', str(name or ''))
    return re.sub(r'\s+', ' ', name).strip(' .')[:60]


def adverse_parties(desk, mid):
    """Parties adverses du dossier, dans l'ordre : registre des conflits (rôle « adverse »), puis fiche du dossier (parties qui ne sont
    pas clientes du cabinet). Doublons retirés (comparaison sans accents ni casse)."""
    names = []
    try:
        for r in desk.db.execute("SELECT name FROM conflicts500_parties WHERE matter=? AND role='adverse' ORDER BY created", (mid,)):
            names.append(r[0])
    except Exception:   # noqa: BLE001 — registre pas encore créé
        pass
    try:
        from . import dossier5627   # 5.6.34 (B04) : la liste corrigée par l'avocat prime sur la version de l'IA
        fiche = dossier5627.latest(desk, mid) or {}
        edit = (fiche.get('edits') or {}).get('parties')
        data = {'parties': dossier5627.parties_from_text(edit['text'])} if edit else (fiche.get('data') or {})
        names += [p.get('nom', '') for p in data.get('parties', []) if not p.get('client_du_cabinet')
                  and p.get('qualite') in ('defendeur', 'intime', 'demandeur', 'appelant', 'intervenant', 'autre')]
    except Exception:   # noqa: BLE001 — fiche absente
        pass
    out, seen = [], set()
    for n in names:
        clean = _clean_name(n)
        if clean and _fold(clean) not in seen:
            seen.add(_fold(clean))
            out.append(clean)
    return out


def _values(desk, m):
    values = {'client': _clean_name(m.get('client_name') or '')}
    for i, name in enumerate(adverse_parties(desk, m['id'])[:9], 1):
        values['partie_adverse%02d' % i] = name
    return values


def _regex(pattern):
    """Forme du dossier : « PIECES ADVERSES - {{partie_adverse01}} » reconnaît tout dossier « PIECES ADVERSES - … »."""
    parts = VAR.split(pattern)
    rx = ''
    for i, part in enumerate(parts):
        rx += re.escape(_fold(part)) if i % 2 == 0 else '.+'
    return re.compile('^' + rx + '$')


def resolve(desk, m):
    """Dossiers de travail de ce dossier client : nom résolu (ou motif d'absence), usage et chemin."""
    values = _values(desk, m)
    root = clean_path(m['path'])
    out = []
    for item in configured(desk):
        missing = [v for v in VAR.findall(item['pattern']) if not values.get(v)]
        name = None if missing else VAR.sub(lambda x: values[x.group(1)], item['pattern'])
        label = USAGES[item['usage']]
        who = [values[v] for v in VAR.findall(item['pattern']) if v.startswith('partie_') and values.get(v)]
        if item['usage'] == 'pieces_adverses' and who and not missing:
            label += ' — ' + ', '.join(who)
        reason = ''
        if missing:
            reason = ', '.join('partie adverse n° %d' % int(v[-2:]) if v.startswith('partie_') else 'nom du client' for v in missing) + ' : non renseigné pour ce dossier'
        out.append({'pattern': item['pattern'], 'usage': item['usage'], 'label': label, 'name': name,
                    'path': clean_path(root + '/' + name) if name else '', 'reason': reason})
    return out


def usage_of(desk, m, path, resolved=None):
    """Usage du dossier de travail qui contient ce document (None s'il n'est dans aucun). Le premier niveau sous le dossier client est
    comparé au nom résolu, puis à la forme du motif (nom de partie écrit autrement)."""
    root = clean_path(m['path'])
    path = clean_path(path)
    if not under(path, root) or path == root:
        return None
    try:
        first = PurePosixPath(path).relative_to(PurePosixPath(root)).parts[0]
    except (ValueError, IndexError):
        return None
    if first == PurePosixPath(path).name:   # fichier posé à la racine du dossier client
        return None
    f = _fold(first)
    resolved = resolved if resolved is not None else resolve(desk, m)
    for r in resolved:
        if r['name'] and _fold(r['name']) == f:
            return {'usage': r['usage'], 'label': r['label'], 'folder': first}
    for r in resolved:
        if _regex(r['pattern']).match(f):
            label = USAGES[r['usage']]
            if r['usage'] == 'pieces_adverses' and ' - ' in first:
                label += ' — ' + first.split(' - ', 1)[1].strip()
            return {'usage': r['usage'], 'label': label, 'folder': first}
    return None


def style_folders(desk, m):
    """Dossiers « écrits du cabinet » à lire pour l'apprentissage du style (les pièces n'y figurent jamais)."""
    return [r['name'] for r in resolve(desk, m) if r['usage'] == 'ecrits' and r['name']]


def state(desk, m, client=None):
    """Dossiers de travail de ce dossier client avec leur présence dans Nextcloud (lecture seule)."""
    rows = resolve(desk, m)
    present = None
    if client is not None:
        try:
            present = {_fold(PurePosixPath(i['path']).name): PurePosixPath(i['path']).name
                       for i in client.list_folder(clean_path(m['path'])) if i.get('directory')}
        except Stop:
            present = None
    for r in rows:
        r['exists'] = None
        if present is not None:
            if r['name'] and _fold(r['name']) in present:
                r['exists'] = True
            else:
                other = next((v for k, v in present.items() if _regex(r['pattern']).match(k)), None)
                r['exists'] = bool(other) if r['name'] is None else False
                if other and r['name'] is None:
                    r['existing'] = other
    return rows


def create_missing(desk, m, client, names=None):
    """Crée dans Nextcloud les dossiers de travail absents de CE dossier client (clic explicite de l'avocat ; jamais d'écrasement)."""
    root = clean_path(m['path'])
    created, skipped = [], []
    for r in state(desk, m, client):
        if not r['name'] or r['exists'] is not False:
            continue
        if names is not None and r['name'] not in names:
            continue
        try:
            client.ensure_folder(r['path'], root)
            created.append(r['name'])
        except Stop as ex:
            skipped.append({'name': r['name'], 'error': str(ex)})
    try:
        desk.audit('dossiers5632_creation', {'matter': m['id'], 'created': created, 'skipped': [s['name'] for s in skipped]})
    except Exception:   # noqa: BLE001
        pass
    return {'created': created, 'skipped': skipped,
            'message': ('%d dossier(s) de travail créé(s).' % len(created)) if created else 'Aucun dossier de travail à créer.'}
