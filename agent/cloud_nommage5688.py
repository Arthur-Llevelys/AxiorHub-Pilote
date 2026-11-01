"""5.6.88 : règle de nommage par nature de document, appliquée aux documents produits et rangés, dans le stockage en service.

Sans règle enregistrée, chaque module garde le nom qu'il produisait : une mise à jour ne renomme rien. Une règle ne touche que les documents
créés ensuite, jamais un fichier existant. Le dépôt reste exclusif : à nom égal, la copie est numérotée, rien n'est écrasé.
"""
from datetime import date
from pathlib import PurePosixPath
import re

from .common import Stop, clean_path

KEY = 'cloud5688:nommage'
NATURES = {'piece_jointe': 'Pièce jointe de courriel', 'courrier': 'Courrier', 'note': 'Note interne', 'document': 'Document produit'}
TOKENS = {'date': 'Date (AAAA-MM-JJ)', 'annee': 'Année', 'mois': 'Mois', 'jour': 'Jour', 'dossier': 'Référence du dossier', 'client': 'Client', 'rg': 'Numéro de RG', 'titre': 'Titre ou nom d’origine',
          'nature': 'Nature du document', 'expediteur': 'Expéditeur'}
TOKEN = re.compile(r'\{([a-z]*)\}')
MAX_NAME = 150
MAX_ATTEMPTS = 30


def _clean(value):
    """Un morceau de nom sûr chez tous les fournisseurs : ni séparateur, ni caractère refusé par Windows, SharePoint ou OneDrive."""
    text = re.sub(r'[\\/:*?"<>|\x00-\x1f]', '_', str(value or ''))
    return re.sub(r'\s+', ' ', text).strip(' .')


def check_rule(rule):
    """Règle saisie par un administrateur : champs connus, modèle sans chemin, sous-dossier relatif et sans remontée."""
    model = str((rule or {}).get('modele') or '').strip()
    folder = str((rule or {}).get('sous_dossier') or '').strip().strip('/')
    if len(model) > 200 or len(folder) > 200:
        raise Stop('regle_nommage_trop_longue')
    if re.search(r'[\\/:*?"<>|\x00-\x1f]', model) or '..' in model:
        raise Stop('regle_nommage_invalide')
    for found in TOKEN.findall(model) + TOKEN.findall(folder):
        if found not in TOKENS:
            raise Stop('regle_nommage_champ_inconnu')
    if folder:
        if '\\' in folder or re.search(r'[:*?"<>|\x00-\x1f]', folder) or any(p in ('', '.', '..') or p.startswith('.') for p in folder.split('/')):
            raise Stop('regle_nommage_sous_dossier_invalide')
    return {'modele': model, 'sous_dossier': folder}


def rules(desk):
    return desk.settings(KEY, {}) or {}


def save(desk, actor, role, nature, rule):
    if role != 'administrateur':
        raise Stop('role_insuffisant')
    if nature not in NATURES:
        raise Stop('nature_document_inconnue')
    stored = dict(rules(desk))
    clean = check_rule(rule)
    if clean['modele'] or clean['sous_dossier']:
        stored[nature] = clean
    else:
        stored.pop(nature, None)
    desk.setting(KEY, stored)
    desk.audit('cloud5688_regle_nommage', {'nature': nature, 'regle': clean, 'par': str(actor)})
    return {'message': 'Règle enregistrée. Elle s’applique aux prochains documents, jamais aux fichiers existants.'}


def context_of(matter, **extra):
    """Champs tirés de la fiche du dossier."""
    matter = matter or {}
    return {'dossier': matter.get('id', ''), 'client': matter.get('client_name', ''), 'rg': matter.get('rg') or matter.get('numero_rg') or '', **extra}


def _values(nature, context):
    today = context.get('date') or date.today()
    today = today if isinstance(today, date) else date.fromisoformat(str(today)[:10])
    values = {'date': today.isoformat(), 'annee': '%04d' % today.year, 'mois': '%02d' % today.month, 'jour': '%02d' % today.day, 'nature': NATURES.get(nature, nature)}
    return {**values, **{k: _clean(context.get(k)) for k in ('dossier', 'client', 'rg', 'titre', 'expediteur')}}


def _render(model, values):
    out = TOKEN.sub(lambda m: values.get(m.group(1), ''), model)
    return re.sub(r'(\s*-\s*){2,}', ' - ', re.sub(r'\s+', ' ', out)).strip(' -_.')


def apply(desk, nature, name, context=None):
    """Nom final d'un document ; l'extension d'origine est toujours gardée. Sans règle, ou si le modèle ne produit rien : le nom d'origine."""
    rule = rules(desk).get(nature) or {}
    if not rule.get('modele'):
        return name
    pure = PurePosixPath(str(name))
    values = _values(nature, {'titre': pure.stem, **(context or {})})
    stem = _clean(_render(rule['modele'], values))[:MAX_NAME - len(pure.suffix)].strip(' .')
    return stem + pure.suffix if stem else name


def folder_for(desk, nature, base, default, context=None):
    """Dossier de rangement : le sous-dossier de la règle, sous le dossier de l'affaire, sinon le dossier habituel."""
    rule = rules(desk).get(nature) or {}
    if not rule.get('sous_dossier'):
        return default
    values = _values(nature, context or {})
    parts = [_clean(TOKEN.sub(lambda m: values.get(m.group(1), ''), p)) for p in rule['sous_dossier'].split('/')]
    parts = [p for p in parts if p and not p.startswith('.')]
    return clean_path(base + '/' + '/'.join(parts)) if parts else default


def numbered(name, n):
    pure = PurePosixPath(name)
    return name if n <= 1 else '%s (%d)%s' % (pure.stem, n, pure.suffix)


def put_unique(client, folder, name, data, content_type='application/octet-stream'):
    """Dépôt exclusif : un nom déjà pris donne une copie numérotée, le fichier existant n'est jamais touché."""
    for n in range(1, MAX_ATTEMPTS + 1):
        target = clean_path(folder + '/' + numbered(name, n))
        try:
            client.put_file(target, data, content_type)
            return target
        except Stop as ex:
            if str(ex) != 'http_412':
                raise
    raise Stop('nom_fichier_indisponible')
