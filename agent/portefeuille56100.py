"""5.6.100 (lot C, R05) : maintenance explicite et auditée du portefeuille, et rapprochement en lecture seule.

Depuis la 5.6.100, aucune page ni aucun résumé n'efface de ligne du portefeuille (``portfolio.portfolio_summary`` et ``portfolio_rows``
lisent ; le travail de fond complète la projection sans rien supprimer). Le seul chemin de suppression est ici, hors de toute requête :

- ``simuler`` : source complète et saine (registre JSON entier, sans filtre de personne ; registre unique ; alias), lignes orphelines
  candidates, lignes portant une décision humaine (jamais supprimées : à revoir), lignes rattachables par un alias (signalées, jamais
  déplacées), et un **jeton** qui résume exactement le plan ;
- ``nettoyer`` : refait la simulation, exige le même jeton (rien n'a changé depuis l'aperçu), refuse toute source incomplète, sauvegarde
  la base (copie vérifiée, place disque contrôlée), supprime les seules lignes du plan dans une transaction et l'inscrit au journal d'audit ;
- ``rapprocher`` : compare, en lecture seule, une sauvegarde antérieure et l'état courant des cinq tables (lignes manquantes,
  différentes, nouvelles), par dossier ;
- ``restaurer_dossier`` : pour UN dossier choisi par une personne, réinsère depuis la sauvegarde les seules lignes manquantes
  (jamais d'écrasement d'une ligne existante), après simulation, sauvegarde et inscription au journal.

L'index des documents permet de recalculer des projections ; il ne prouve pas le rétablissement d'une décision humaine : une épingle, un
état confirmé ou un rattachement confirmé ne se reconstruisent jamais par heuristique, seulement par ``restaurer_dossier``.
"""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import uuid

from .common import Stop, load_matters

TABLES = ('matter_portfolio', 'portfolio_mail_links', 'association_groups', 'portfolio_document_activity', 'portfolio_calendar_links')
CLES = {'matter_portfolio': 'matter', 'portfolio_mail_links': 'id', 'association_groups': 'id', 'portfolio_document_activity': 'matter',
        'portfolio_calendar_links': 'id'}
DOSSIER_SAUVEGARDES = 'maintenance56100'
LIMITE_LISTE = 500


def _maintenant():
    return datetime.now(timezone.utc).isoformat()


def _table(db, nom):
    return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (nom,)).fetchone() is not None


def _decision_humaine(table, row):
    """Une ligne qui porte une décision d'une personne n'est jamais supprimée par la maintenance."""
    if table == 'matter_portfolio':
        return bool(row['manual_state']) or bool(row['pinned'])
    if table == 'portfolio_mail_links':
        return row['status'] == 'confirmed' or row['source'] == 'confirmation_avocat'
    if table == 'association_groups':
        return row['status'] in ('confirmed', 'rejected')
    return False


def _hors_requete():
    """La maintenance ne s'exécute jamais dans le contexte d'une personne (registre filtré : un dossier masqué paraîtrait supprimé)."""
    from .membres5650 import current_author, viewer_hidden
    if viewer_hidden() or current_author():
        raise Stop('maintenance_hors_requete')


def source(desk):
    """Source complète : identifiants connus (registre JSON entier + registre unique), alias, problèmes (vide si la source est saine)."""
    problemes, ids, alias = [], set(), {}
    try:
        ids |= {str(m['id']) for m in load_matters(desk.c, unfiltered=True)}
    except Exception as ex:   # noqa: BLE001 — registre illisible : aucune suppression possible
        problemes.append('registre_illisible:%s' % str(ex).split(':')[0][:80])
    try:
        if _table(desk.db, 'dossiers5697'):
            ids |= {str(r[0]) for r in desk.db.execute('SELECT id FROM dossiers5697')}
        if _table(desk.db, 'dossier_alias5697'):
            alias = {str(r[0]): str(r[1]) for r in desk.db.execute("SELECT alias,dossier_id FROM dossier_alias5697 WHERE genre IN ('id','ref','chemin')")}
    except sqlite3.Error as ex:
        problemes.append('registre_unique_illisible:%s' % str(ex)[:80])
    if not ids:
        problemes.append('registre_vide')
    manquantes = [t for t in TABLES if not _table(desk.db, t)]
    if manquantes:
        problemes.append('tables_absentes:' + ','.join(manquantes))
    return {'ids': ids, 'alias': alias, 'problemes': problemes}


def simuler(desk):
    """Aperçu (aucune écriture) : candidats à la suppression, décisions humaines conservées, lignes rattachables, jeton du plan."""
    _hors_requete()
    src = source(desk)
    candidats, decisions, rattachables = [], [], []
    if not any(p.startswith('tables_absentes') for p in src['problemes']):
        for t in TABLES:
            for row in desk.db.execute('SELECT * FROM %s ORDER BY %s' % (t, CLES[t])):
                mid = str(row['matter'])
                if mid in src['ids']:
                    continue
                ligne = {'table': t, 'cle': str(row[CLES[t]]), 'dossier': mid}
                if mid in src['alias'] and src['alias'][mid] in src['ids']:
                    rattachables.append({**ligne, 'dossier_courant': src['alias'][mid]})
                elif _decision_humaine(t, row):
                    decisions.append(ligne)
                else:
                    candidats.append(ligne)
    jeton = hashlib.sha256(json.dumps(candidats, sort_keys=True).encode('utf-8')).hexdigest()[:32]
    return {'source': {'saine': not src['problemes'], 'problemes': src['problemes'], 'dossiers_connus': len(src['ids'])},
            'candidats': candidats, 'decisions_conservees': decisions, 'rattachables': rattachables, 'jeton': jeton,
            'limites': ['Une ligne qui porte une décision humaine (épingle, état confirmé, rattachement confirmé) n’est jamais supprimée.',
                        'Une ligne d’un ancien identifiant (alias) est signalée, jamais déplacée automatiquement.',
                        'Rien n’est supprimé si le registre est incomplet ou illisible.']}


def _dossier_sauvegardes(desk):
    p = Path(desk.c['state_dir']) / DOSSIER_SAUVEGARDES
    p.mkdir(mode=0o700, exist_ok=True)
    return p


def sauvegarder(desk, motif):
    """Copie cohérente et vérifiée de la base (API de sauvegarde SQLite) avant toute écriture de maintenance. Place disque contrôlée."""
    chemin_base = Path(desk.c['state_dir']) / 'desk.sqlite3'
    dossier = _dossier_sauvegardes(desk)
    taille = chemin_base.stat().st_size if chemin_base.exists() else 0
    if shutil.disk_usage(dossier).free < 2 * taille + 10 * 1024 * 1024:
        raise Stop('stockage_insuffisant')
    cible = dossier / ('portefeuille-%s-%s-%s.sqlite3' % (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'), motif, uuid.uuid4().hex[:8]))
    if desk.db.in_transaction:
        desk.db.commit()
    dst = sqlite3.connect(cible)
    try:
        desk.db.backup(dst)
        dst.commit()
        if dst.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise Stop('sauvegarde_invalide')
        for t in TABLES:
            if _table(desk.db, t) and (dst.execute('SELECT COUNT(*) FROM %s' % t).fetchone()[0] != desk.db.execute('SELECT COUNT(*) FROM %s' % t).fetchone()[0]):
                raise Stop('sauvegarde_invalide')
    finally:
        dst.close()
    try:
        cible.chmod(0o600)
    except OSError:
        pass
    return cible


def nettoyer(desk, jeton, operateur):
    """Suppression des seules lignes du plan simulé (même jeton), source saine, après sauvegarde vérifiée ; inscrite au journal d'audit."""
    operateur = str(operateur or '').strip()[:120]
    if not operateur:
        raise Stop('operateur_requis')
    plan = simuler(desk)
    if not plan['source']['saine']:
        raise Stop('source_incomplete')
    if str(jeton or '') != plan['jeton']:
        raise Stop('simulation_perimee')
    if not plan['candidats']:
        return {'supprimees': 0, 'sauvegarde': '', 'message': 'Rien à supprimer.'}
    sauvegarde = sauvegarder(desk, 'avant-nettoyage')
    desk.db.execute('BEGIN IMMEDIATE')
    try:
        connus = source(desk)
        if connus['problemes']:
            raise Stop('source_incomplete')
        n = 0
        for c in plan['candidats']:
            if c['dossier'] in connus['ids']:   # réapparu entre l'aperçu et l'exécution : conservé
                continue
            n += desk.db.execute('DELETE FROM %s WHERE %s=? AND matter=?' % (c['table'], CLES[c['table']]), (c['cle'], c['dossier'])).rowcount
        desk.db.commit()
    except BaseException:
        desk.db.rollback()
        raise
    desk.audit('portefeuille56100_nettoyage', {'operateur': operateur, 'supprimees': n, 'jeton': plan['jeton'], 'sauvegarde': sauvegarde.name,
                                               'decisions_conservees': len(plan['decisions_conservees']), 'rattachables': len(plan['rattachables'])})
    return {'supprimees': n, 'sauvegarde': str(sauvegarde), 'decisions_conservees': len(plan['decisions_conservees']),
            'rattachables': len(plan['rattachables'])}


# ------------------------------------------------------------------------------------------------ rapprochement (lecture seule)
def _lecture(chemin):
    uri = Path(chemin).resolve(strict=True).as_uri() + '?mode=ro'
    db = sqlite3.connect(uri, uri=True)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA query_only=ON')
    return db


def rapprocher(sauvegarde, courant, dossier=''):
    """Compare une sauvegarde antérieure et l'état courant des cinq tables, sans aucune écriture (bases ouvertes en lecture seule).
    Par table : lignes manquantes (présentes dans la sauvegarde, absentes aujourd'hui), différentes (mêmes clés, valeurs changées),
    nouvelles (nombre) ; et un résumé par dossier. Les décisions humaines manquantes sont signalées comme telles."""
    a, b = _lecture(sauvegarde), _lecture(courant)
    try:
        out, par_dossier = {}, {}
        for t in TABLES:
            if not _table(a, t):
                out[t] = {'absente_de_la_sauvegarde': True}
                continue
            filtre, args = (' WHERE matter=?', (dossier,)) if dossier else ('', ())
            anciens = {str(r[CLES[t]]): r for r in a.execute('SELECT * FROM %s%s' % (t, filtre), args)}
            actuels = {str(r[CLES[t]]): r for r in b.execute('SELECT * FROM %s%s' % (t, filtre), args)} if _table(b, t) else {}
            manquantes, differentes = [], []
            for cle, r in sorted(anciens.items()):
                if cle not in actuels:
                    manquantes.append({'cle': cle, 'dossier': str(r['matter']), 'decision_humaine': _decision_humaine(t, r)})
                else:
                    colonnes = [k for k in r.keys() if k in actuels[cle].keys() and r[k] != actuels[cle][k] and k not in ('updated', 'checked')]
                    if colonnes:
                        differentes.append({'cle': cle, 'dossier': str(r['matter']), 'colonnes': colonnes})
            for x in manquantes:
                d = par_dossier.setdefault(x['dossier'], {'manquantes': 0, 'decisions_manquantes': 0, 'differentes': 0})
                d['manquantes'] += 1
                d['decisions_manquantes'] += int(x['decision_humaine'])
            for x in differentes:
                par_dossier.setdefault(x['dossier'], {'manquantes': 0, 'decisions_manquantes': 0, 'differentes': 0})['differentes'] += 1
            out[t] = {'manquantes': manquantes[:LIMITE_LISTE], 'nombre_manquantes': len(manquantes), 'differentes': differentes[:LIMITE_LISTE],
                      'nombre_differentes': len(differentes), 'nouvelles': len(set(actuels) - set(anciens))}
        return {'tables': out, 'par_dossier': dict(sorted(par_dossier.items())),
                'limites': ['Lecture seule : aucune ligne n’a été modifiée.',
                            'Une ligne manquante n’est pas forcément une perte : vérifiez le dossier avant de la rétablir (restaurer_dossier).',
                            'L’index des documents ne prouve pas le rétablissement d’une décision humaine.']}
    finally:
        a.close()
        b.close()


def restaurer_dossier(desk, sauvegarde, dossier, operateur, appliquer=False):
    """Pour UN dossier : réinsère depuis la sauvegarde les lignes manquantes des cinq tables (INSERT seul, jamais d'écrasement).
    ``appliquer=False`` : simulation. Le dossier doit exister dans le registre complet ; sauvegarde de l'état courant et journal d'audit."""
    _hors_requete()
    dossier = str(dossier or '').strip()
    operateur = str(operateur or '').strip()[:120]
    if not dossier:
        raise Stop('dossier_requis')
    src = source(desk)
    if src['problemes']:
        raise Stop('source_incomplete')
    if dossier not in src['ids']:
        raise Stop('dossier_absent_du_registre')
    a = _lecture(sauvegarde)
    try:
        lignes = []
        for t in TABLES:
            if not _table(a, t):
                continue
            actuels = {str(r[0]) for r in desk.db.execute('SELECT %s FROM %s WHERE matter=?' % (CLES[t], t), (dossier,))}
            colonnes = [r[1] for r in desk.db.execute('PRAGMA table_info(%s)' % t)]
            for r in a.execute('SELECT * FROM %s WHERE matter=?' % t, (dossier,)):
                if str(r[CLES[t]]) not in actuels and not desk.db.execute('SELECT 1 FROM %s WHERE %s=?' % (t, CLES[t]), (r[CLES[t]],)).fetchone():
                    lignes.append((t, {k: r[k] for k in r.keys() if k in colonnes}))
    finally:
        a.close()
    apercu = [{'table': t, 'cle': str(v[CLES[t]])} for t, v in lignes]
    if not appliquer or not lignes:
        return {'dossier': dossier, 'a_restaurer': apercu, 'applique': False}
    if not operateur:
        raise Stop('operateur_requis')
    copie = sauvegarder(desk, 'avant-restauration')
    desk.db.execute('BEGIN IMMEDIATE')
    try:
        n = 0
        for t, v in lignes:
            n += desk.db.execute('INSERT OR IGNORE INTO %s(%s) VALUES(%s)' % (t, ','.join(v), ','.join('?' * len(v))), list(v.values())).rowcount
        desk.db.commit()
    except BaseException:
        desk.db.rollback()
        raise
    desk.audit('portefeuille56100_restauration', {'operateur': operateur, 'dossier': dossier, 'restaurees': n, 'sauvegarde_source': Path(sauvegarde).name,
                                                  'sauvegarde_avant': copie.name})
    return {'dossier': dossier, 'a_restaurer': apercu, 'applique': True, 'restaurees': n, 'sauvegarde': str(copie)}
