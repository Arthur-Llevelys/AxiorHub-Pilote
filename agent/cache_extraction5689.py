"""5.6.89 : cache des extractions (chapitre 44 du cahier).

Une extraction se réutilise à l'identique si, et seulement si, la source, l'extracteur, la compétence, les paramètres utiles, le cabinet et le
périmètre d'accès sont les mêmes. La clé les contient tous : un cache n'est jamais partagé entre cabinets.

Chaque lecture passe la décision commune : compte révoqué, dossier réservé, politique illisible ou plus restrictive : ni valeur, ni titre, ni
compteur. Une correction, un changement de droit ou une révocation invalident les entrées concernées, qui restent tracées sans leur contenu.
"""
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import uuid

from .common import Stop

SCHEMA = '''
CREATE TABLE IF NOT EXISTS extraction_cache5689(
  cle TEXT PRIMARY KEY, cabinet TEXT NOT NULL, empreinte_source TEXT NOT NULL, extracteur TEXT NOT NULL, competence TEXT NOT NULL, parametres_h TEXT NOT NULL,
  perimetre TEXT NOT NULL, matter TEXT NOT NULL DEFAULT '', revision INTEGER NOT NULL, valeur TEXT, cree TEXT NOT NULL, invalide_le TEXT, motif TEXT NOT NULL DEFAULT '');
CREATE INDEX IF NOT EXISTS extraction_cache5689_matter ON extraction_cache5689(matter,invalide_le);
CREATE INDEX IF NOT EXISTS extraction_cache5689_source ON extraction_cache5689(empreinte_source);
'''
GARDER_JOURS = 365


def _connect(state_dir):
    db = sqlite3.connect(Path(state_dir) / 'desk.sqlite3', timeout=10)
    db.row_factory = sqlite3.Row
    db.executescript(SCHEMA)
    return db


def perimetre(state_dir, matter):
    """Empreinte des droits sur le dossier : qui en est écarté. Un changement de réservation change la clé."""
    from .membres5650 import load
    try:
        reserves = (load(state_dir) or {}).get('reserves') or {}
    except Stop:
        raise
    owners = reserves.get(str(matter)) if isinstance(reserves, dict) else None
    return hashlib.sha256(json.dumps(sorted(owners or []) if isinstance(owners, list) else owners, sort_keys=True, default=str).encode()).hexdigest()[:16]


def cle(config, empreinte, extracteur, competence='', parametres=None, matter=''):
    """Clé complète : empreinte source, version d'extracteur, compétence et sa version, paramètres utiles, cabinet, périmètre d'accès."""
    state_dir = config['state_dir']
    cabinet = str(config.get('cabinet_id') or 'cabinet')
    parametres_h = hashlib.sha256(json.dumps(parametres or {}, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]
    zone = perimetre(state_dir, matter) if matter else 'cabinet'
    brut = '|'.join((cabinet, str(empreinte), str(extracteur), str(competence), parametres_h, zone, str(matter)))
    return hashlib.sha256(brut.encode()).hexdigest(), cabinet, parametres_h, zone


def _admis(config, matter, acteur, role, destination, local):
    from .data_policy5689 import evaluer
    return evaluer(config, 'classement', destination, finalite='lecture_cache', dossiers=(matter,) if matter else (), labels=('dossier',) if matter else (), acteur=acteur, role=role,
                   local=local, journal=False)['allowed']


def ecrire(desk, empreinte, extracteur, valeur, competence='', parametres=None, matter='', destination='ollama', local=True):
    """Une extraction d'un service que la politique refuse n'est pas mise en cache. Rend la clé, ou None."""
    config = desk.c
    if not _admis(config, matter, '', '', destination, local):
        return None
    ident, cabinet, parametres_h, zone = cle(config, empreinte, extracteur, competence, parametres, matter)
    from .data_policy5689 import charger
    revision = charger(config['state_dir'])['revision']
    db = _connect(config['state_dir'])
    try:
        db.execute('INSERT OR REPLACE INTO extraction_cache5689(cle,cabinet,empreinte_source,extracteur,competence,parametres_h,perimetre,matter,revision,valeur,cree) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                   (ident, cabinet, str(empreinte), str(extracteur), str(competence), parametres_h, zone, str(matter), revision, json.dumps(valeur, ensure_ascii=False), datetime.now(timezone.utc).isoformat()))
        db.execute('DELETE FROM extraction_cache5689 WHERE cree<?', ((datetime.now(timezone.utc) - timedelta(days=GARDER_JOURS)).isoformat(),))
        db.commit()
    finally:
        db.close()
    return ident


def lire(desk, empreinte, extracteur, competence='', parametres=None, matter='', acteur='', role='', destination='ollama', local=True):
    """Valeur en cache ou None. Le contrôle précède la lecture : un compte sans droit n'apprend même pas qu'une entrée existe."""
    config = desk.c
    if not _admis(config, matter, acteur, role, destination, local):
        return None
    ident = cle(config, empreinte, extracteur, competence, parametres, matter)[0]
    db = _connect(config['state_dir'])
    try:
        row = db.execute('SELECT valeur FROM extraction_cache5689 WHERE cle=? AND invalide_le IS NULL', (ident,)).fetchone()
    finally:
        db.close()
    return json.loads(row['valeur']) if row and row['valeur'] is not None else None


def compter(desk, matter, acteur, role):
    """Compteur soumis à la même décision : 0 pour un compte sans droit, sans distinguer « rien » de « refusé »."""
    if not _admis(desk.c, matter, acteur, role, 'ollama', True):
        return 0
    db = _connect(desk.c['state_dir'])
    try:
        return int(db.execute('SELECT COUNT(*) FROM extraction_cache5689 WHERE matter=? AND invalide_le IS NULL', (str(matter),)).fetchone()[0])
    finally:
        db.close()


def invalider(desk, matter=None, empreinte=None, motif='correction', sous_revision=None, extracteur=None):
    """Entrées invalidées : contenu effacé, trace conservée. ``sous_revision`` vise tout ce qui a été produit sous une politique antérieure."""
    return invalider_base(desk.c['state_dir'], matter, empreinte, motif, sous_revision, extracteur)


def invalider_base(state_dir, matter=None, empreinte=None, motif='correction', sous_revision=None, extracteur=None):
    conditions, valeurs = ['invalide_le IS NULL'], []
    for colonne, valeur, signe in (('matter', matter, '='), ('empreinte_source', empreinte, '='), ('revision', sous_revision, '<'), ('extracteur', extracteur, '=')):
        if valeur is not None:
            conditions.append('%s%s?' % (colonne, signe))
            valeurs.append(valeur)
    if len(conditions) == 1 and motif != 'purge':
        raise Stop('invalidation_sans_cible')
    if not state_dir or not (Path(state_dir) / 'desk.sqlite3').is_file():
        return 0
    db = _connect(state_dir)
    try:
        n = db.execute('UPDATE extraction_cache5689 SET valeur=NULL,invalide_le=?,motif=? WHERE ' + ' AND '.join(conditions), [datetime.now(timezone.utc).isoformat(), str(motif)[:60]] + valeurs).rowcount
        db.commit()
        return n
    finally:
        db.close()


def sur_droits(state_dir, matter):
    """Appelé quand les droits d'un dossier changent (réservation, révocation)."""
    try:
        return invalider_base(state_dir, matter=str(matter), motif='droits_modifies')
    except sqlite3.Error:
        return 0


def apercu(desk, matter, acteur, role):
    """Métadonnées pour le détail, jamais la valeur ; rien pour un compte sans droit."""
    if not _admis(desk.c, matter, acteur, role, 'ollama', True):
        return []
    db = _connect(desk.c['state_dir'])
    try:
        return [dict(r) for r in db.execute('SELECT extracteur,competence,cree,invalide_le,motif FROM extraction_cache5689 WHERE matter=? ORDER BY cree DESC LIMIT 100', (str(matter),))]
    finally:
        db.close()


def nouvelle_cle_idempotence():
    return uuid.uuid4().hex
