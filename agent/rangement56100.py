"""5.6.100 (lot D, cahier § 13 « Rangement ») : type explicite de chaque répertoire — Affaire, Cabinet ou Dépôt.

La 5.6.99 retirait de la liste les répertoires dont le NOM ressemblait à un dépôt (« 09 - Scanner », « A classer »…). Le cabinet garde
ce traitement pour les répertoires de dépôt (décision 3 du 13 novembre 2026), mais :
- un type explicite, choisi par l'administrateur (« Affaire », « Cabinet », « Dépôt »), prime toujours sur la règle de nom ; il est
  proposé à l'import (simulation d'abord, choix modifiable ligne par ligne) et modifiable ensuite pour les dossiers déjà importés ;
- sans choix explicite, une ressemblance de nom ne retire JAMAIS une vraie affaire : un dossier du registre qui a un client identifié,
  un numéro RG, une partie autre que le client, ou qui a été créé à la main (provenance manuelle, courriel, IA, connecteur) reste listé ;
- un répertoire « Cabinet » (administratif, modèles, comptabilité…) n'est que PROPOSÉ : il reste listé tant qu'aucun choix n'est fait ;
- rien n'est supprimé ni déplacé : un répertoire Cabinet ou Dépôt reste indexé et recherchable comme fichiers ; le registre JSON, l'index
  et les liens historiques (``/dossier?id=…``) sont conservés. Le choix est réversible.
"""
import re
import sqlite3
from datetime import datetime, timezone

from .common import Stop, fold

TYPES = {'affaire': 'Affaire', 'cabinet': 'Cabinet', 'depot': 'Dépôt'}
SCHEMA = '''
CREATE TABLE IF NOT EXISTS dossier_types56100(
  cabinet TEXT NOT NULL, cle TEXT NOT NULL, type TEXT NOT NULL CHECK (type IN ('affaire','cabinet','depot')),
  auteur TEXT NOT NULL DEFAULT '', quand TEXT NOT NULL, PRIMARY KEY(cabinet, cle));
'''
# Noms de répertoires internes du cabinet (proposition seulement, jamais une élimination)
CABINET_NAMES = frozenset(('administratif', 'administration', 'cabinet', 'gestion du cabinet', 'gestion cabinet', 'modeles', 'modele', 'trames',
                           'comptabilite', 'compta', 'ressources humaines', 'rh', 'formation', 'formations', 'bibliotheque', 'documentation',
                           'archives cabinet', 'divers', 'interne', 'internes', 'templates', 'ordre des avocats', 'barreau', 'assurance cabinet'))
PROVENANCES_AFFAIRE = ('manuel', 'courriel', 'ia', 'connecteur')


def ensure_schema(desk):
    """Table additive ; appelée par ``Desk`` à la préparation de la base."""
    if desk.db.in_transaction:
        desk.db.commit()
    desk.db.executescript(SCHEMA)
    desk.db.commit()


def _cabinet(desk):
    try:
        return str(desk.settings('cabinet5689:id', '') or 'cabinet')
    except Exception:   # noqa: BLE001
        return 'cabinet'


def _base(texte):
    nom = str(texte or '').rstrip('/').rsplit('/', 1)[-1]
    nom = re.sub(r'^\s*\d{1,3}\s*[-–_.)]\s*', '', nom)
    return re.sub(r'[\s_]+', ' ', fold(nom)).strip()


def nom_cabinet(texte):
    return _base(texte) in CABINET_NAMES


def types(desk, ids=None):
    """{clé: type} des choix explicites (tous si ``ids`` est None). Table absente : aucun choix."""
    try:
        if ids is None:
            rows = desk.db.execute('SELECT cle,type FROM dossier_types56100 WHERE cabinet=?', (_cabinet(desk),)).fetchall()
        else:
            ids = sorted({str(i) for i in ids if i})
            if not ids:
                return {}
            rows = desk.db.execute('SELECT cle,type FROM dossier_types56100 WHERE cabinet=? AND cle IN (%s)' % ','.join('?' * len(ids)),
                                   (_cabinet(desk), *ids)).fetchall()
    except sqlite3.Error:
        return {}
    return {r[0]: r[1] for r in rows}


# ------------------------------------------------------------------------------------------------ règles
def signes_affaire(row, parties_autres=False):
    """Une ligne du registre unique porte-t-elle des signes d'une vraie affaire ? (client identifié qui n'est pas un nom de dépôt,
    numéro RG, partie autre que le client, création à la main)."""
    from .bruit5668 import depot_label
    client = str(row.get('client') or '').strip()
    return bool((client and not depot_label(client) and not nom_cabinet(client)) or str(row.get('rg') or '').strip() or parties_autres
                or row.get('provenance') in PROVENANCES_AFFAIRE)


def proposer_registre(row):
    """Type proposé pour une ligne du registre unique : (type, motif)."""
    from .bruit5668 import depot_label
    if signes_affaire(row):
        return 'affaire', 'client, RG ou création à la main'
    if depot_label(row.get('chemin')) or depot_label(row.get('titre')):
        return 'depot', 'nom d’un répertoire de dépôt'
    if nom_cabinet(row.get('chemin')) or nom_cabinet(row.get('titre')):
        return 'cabinet', 'nom d’un répertoire interne du cabinet'
    return 'affaire', ''


def proposer_historique(m):
    """Type proposé pour un dossier du registre JSON (avant import) : (type, motif)."""
    from .bruit5668 import depot_folder
    m = m or {}
    if depot_folder(m):
        return 'depot', 'nom d’un répertoire de dépôt'
    if nom_cabinet(m.get('path')) or nom_cabinet(m.get('name')):
        return 'cabinet', 'nom d’un répertoire interne du cabinet'
    return 'affaire', ''


def exclu_registre(row, explicite=None):
    """Vrai si la ligne du registre unique ne figure pas dans la liste des affaires.
    Choix explicite : il décide. Sinon : seulement un dépôt reconnu par son nom ET sans aucun signe d'affaire (règle 5.6.99 bornée)."""
    if explicite in TYPES:
        return explicite != 'affaire'
    from .bruit5668 import depot_label
    return (depot_label(row.get('chemin')) or depot_label(row.get('titre'))) and not signes_affaire(row)


def exclu_historique(m, explicite=None):
    """Même règle pour un dossier du registre JSON pas encore importé."""
    if explicite in TYPES:
        return explicite != 'affaire'
    from .bruit5668 import depot_folder
    return depot_folder(m or {})


# ------------------------------------------------------------------------------------------------ écriture (administrateur)
def choisir(desk, acteur, choix, cle):
    """Enregistre des types explicites ``{clé: type}`` (administrateur). Idempotent par clé de commande ; aucun dossier n'est supprimé
    ni déplacé, aucun fichier touché. Rend ``{'enregistres': n, 'types': {...}}``."""
    from . import dossiers5697 as D
    from .acces5697 import acteur_de
    a = acteur_de(acteur)
    if a.role != 'administrateur':
        raise Stop('role_insuffisant')
    if not isinstance(choix, dict) or not choix or len(choix) > 2000:
        raise D.Refus('champs_invalides', {'types': 'Choisissez au moins un type.'})
    propre = {}
    for k, v in choix.items():
        k, v = str(k), str(v)
        if not D.ID_OK.fullmatch(k) or v not in TYPES:
            raise D.Refus('champs_invalides', {'types': 'Type inconnu.'})
        propre[k] = v
    D.ensure_schema(desk)
    cabinet = _cabinet(desk)
    with D._transaction(desk):
        empreinte, recu = D._commande(desk, cabinet, a, 'types56100', cle, propre)
        if recu:
            return recu
        quand = datetime.now(timezone.utc).isoformat(timespec='seconds')
        avant = types(desk, propre)
        for k, v in propre.items():
            desk.db.execute('INSERT INTO dossier_types56100(cabinet,cle,type,auteur,quand) VALUES(?,?,?,?,?) '
                            'ON CONFLICT(cabinet,cle) DO UPDATE SET type=excluded.type, auteur=excluded.auteur, quand=excluded.quand',
                            (cabinet, k, v, a.compte, quand))
            if avant.get(k) != v and desk.db.execute('SELECT 1 FROM dossiers5697 WHERE id=?', (k,)).fetchone():
                D._journal(desk, k, a.compte, 'type_repertoire', {'avant': avant.get(k, ''), 'apres': v})
        recu = {'enregistres': len(propre), 'types': propre, 'message': 'Types enregistrés. Aucun fichier n’a été déplacé ni supprimé.'}
        D._enregistrer_commande(desk, cabinet, a, 'types56100', cle, empreinte, recu)
    try:
        desk.audit('rangement56100_types', {'par': a.compte, 'nombre': len(propre)})
    except Exception:   # noqa: BLE001 — journal d'audit indisponible : le choix est déjà enregistré
        pass
    return recu


def enregistrer_dans_transaction(desk, a, choix):
    """Types choisis sur l'écran d'import, écrits dans la transaction de l'import (``dossiers5697.migrer``)."""
    cabinet = _cabinet(desk)
    quand = datetime.now(timezone.utc).isoformat(timespec='seconds')
    for k, v in choix.items():
        desk.db.execute('INSERT INTO dossier_types56100(cabinet,cle,type,auteur,quand) VALUES(?,?,?,?,?) '
                        'ON CONFLICT(cabinet,cle) DO UPDATE SET type=excluded.type, auteur=excluded.auteur, quand=excluded.quand',
                        (cabinet, k, v, a.compte, quand))


def choix_valides(choix):
    """Filtre un dictionnaire reçu du formulaire d'import : seules les clés valides et les types connus sont gardés."""
    from .dossiers5697 import ID_OK
    if not isinstance(choix, dict):
        return {}
    return {str(k): str(v) for k, v in list(choix.items())[:5000] if ID_OK.fullmatch(str(k)) and str(v) in TYPES}


def repertoires_internes(desk):
    """Dossiers du registre unique à typer : ceux qu'une règle de nom désigne et ceux qui ont déjà un type explicite (écran d'import)."""
    from . import dossiers5697 as D
    from .bruit5668 import depot_label
    D.ensure_schema(desk)
    explicites = types(desk)
    out = []
    for r in desk.db.execute('SELECT id,ref,titre,chemin,client,rg,provenance FROM dossiers5697 ORDER BY titre'):
        row = dict(r)
        propose, motif = proposer_registre(row)
        nom = depot_label(row['chemin']) or depot_label(row['titre']) or nom_cabinet(row['chemin']) or nom_cabinet(row['titre'])
        if row['id'] in explicites or nom:
            out.append({'id': row['id'], 'reference': row['ref'], 'titre': row['titre'], 'chemin': row['chemin'], 'propose': propose,
                        'motif': motif, 'type': explicites.get(row['id'], ''), 'exclu': exclu_registre(row, explicites.get(row['id']))})
    return out
