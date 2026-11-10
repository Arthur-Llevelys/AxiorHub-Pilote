#!/usr/bin/env python3
"""Contrôle en lecture seule des échéances déjà enregistrées (AxiorHub 5.6.96, D06).

  python scripts/audit-echeances-5696.py /chemin/vers/desk.sqlite3           # rapport lisible
  python scripts/audit-echeances-5696.py /chemin/vers/desk.sqlite3 --json    # même rapport en JSON

Travailler de préférence sur une COPIE cohérente de la base (sauvegarde du jour). Le script ouvre la base en lecture seule
(``mode=ro`` et ``PRAGMA query_only``) : il n'écrit ni dans la base ni dans un agenda, ne contacte aucun service et ne corrige rien.
Les décisions restent individuelles, une à une, depuis la page Échéances (Corriger la date, Écarter une fausse détection,
Annuler une échéance devenue sans objet) puis, si besoin, le retrait de l'événement d'agenda.

Il signale :
- les échéances dont la date de départ précède la déclaration d'appel connue (profil de procédure du dossier, ou suivi) ;
- celles calculées au fond depuis un acte d'incident ou un calendrier de la mise en état (requalification de l'extrait source) ;
- les détections automatiques encore ouvertes dont l'extrait ne prouve pas la notification à la date retenue ;
- les propositions non confirmées déjà inscrites à l'agenda par une version antérieure.

Ce qu'il ne prouve pas : il ne relit que l'extrait conservé (300 caractères), pas la pièce entière ; il ne lit pas l'agenda en ligne
(« inscrite » veut dire qu'un identifiant d'événement est noté dans la base) ; une échéance absente de la liste n'est pas pour
autant juste : seule la vérification par l'avocat la rend certaine.
"""
import argparse
from datetime import date
import json
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent.echeances450 import extract_start_events, qualifier_acte   # noqa: E402 : chemin du dépôt ajouté juste au-dessus

FOND = ('conclusions_intime', 'appel_incident_intime', 'bref_delai_intime', 'conclusions_appelant', 'bref_delai_appelant')
INSCRITES = ('confirmee', 'manuel')
LIBELLES = {'depart_avant_declaration': 'date de départ antérieure à la déclaration d’appel connue',
            'fond_depuis_incident': 'délai au fond calculé depuis un acte d’incident ou un calendrier',
            'notification_non_prouvee': 'notification non prouvée par l’extrait source',
            'proposition_inscrite_agenda': 'proposition non confirmée déjà inscrite à l’agenda'}
LIMITES = ('Seul l’extrait conservé (300 caractères) est relu, pas la pièce entière.',
           'L’agenda en ligne n’est pas lu : « inscrite » signifie qu’un identifiant d’événement est noté dans la base.',
           'Une échéance absente de cette liste n’est pas pour autant certaine : seule la vérification par l’avocat la rend certaine.',
           'Rien n’a été modifié : les décisions se prennent une à une sur la page Échéances.')


def _declaration(db, matter):
    """Déclaration d'appel connue : profil de procédure (réglage ``procedure568:case:<dossier>``), sinon déclaration au suivi."""
    row = db.execute('SELECT value FROM settings WHERE key=?', ('procedure568:case:' + matter,)).fetchone() if _table(db, 'settings') else None
    if row:
        try:
            declared = str((json.loads(row[0]) or {}).get('declaration_date') or '')
        except ValueError:
            declared = ''
        if declared:
            return declared
    row = db.execute("SELECT start_date FROM deadlines450 WHERE matter=? AND start_event='declaration_appel' AND status NOT IN ('annulee') "
                     'ORDER BY start_date DESC LIMIT 1', (matter,)).fetchone()
    return row[0] if row else ''


def _table(db, name):
    return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def constats(path):
    """Liste des constats, sans aucune écriture. ``path`` : fichier SQLite (copie de desk.sqlite3)."""
    uri = Path(path).resolve(strict=True).as_uri() + '?mode=ro'
    db = sqlite3.connect(uri, uri=True)
    try:
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA query_only=ON')
        if not _table(db, 'deadlines450'):
            raise SystemExit('Table deadlines450 absente : ce n’est pas une base AxiorHub ou aucune échéance n’a jamais été enregistrée.')
        colonnes = {r[1] for r in db.execute('PRAGMA table_info(deadlines450)')}
        out = []
        for r in db.execute("SELECT * FROM deadlines450 WHERE status NOT IN ('terminee','annulee') ORDER BY matter, due"):
            motifs = []
            if r['calendar_uid'] and r['status'] not in INSCRITES:
                motifs.append('proposition_inscrite_agenda')
            declared = _declaration(db, r['matter'])
            if declared and r['start_event'] != 'declaration_appel' and r['start_date'] < declared:
                motifs.append('depart_avant_declaration')
            if r['source_excerpt'] or r['source_path']:
                q = qualifier_acte(r['source_excerpt'], r['source_path'])
                if q['sans_calcul'] and r['rule_id'] in FOND:
                    motifs.append('fond_depuis_incident')
                elif r['origin'] == 'auto' and r['status'] not in INSCRITES:
                    try:
                        jour = date.fromisoformat(r['start_date'])
                    except ValueError:
                        jour = None
                    trouves = extract_start_events(r['source_excerpt'], jour, r['source_path'], q) if jour else []
                    if not any(e['event'] == r['start_event'] and e['date'] == jour and e['nature_date'] != 'redaction' for e in trouves):
                        motifs.append('notification_non_prouvee')
            if motifs:
                out.append({'id': r['id'], 'dossier': r['matter'], 'regle': r['rule_id'], 'evenement': r['start_event'], 'depart': r['start_date'],
                            'echeance': r['due'], 'statut': r['status'], 'origine': r['origin'], 'agenda': r['calendar_state'] or '',
                            'revision': r['revision'] if 'revision' in colonnes else None, 'constats': motifs})
        return out
    finally:
        db.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description='Contrôle en lecture seule des échéances (rien n’est écrit).')
    parser.add_argument('base', type=Path, help='copie cohérente de desk.sqlite3')
    parser.add_argument('--json', action='store_true', help='sortie JSON')
    args = parser.parse_args(argv)
    lignes = constats(args.base)
    if args.json:
        print(json.dumps({'constats': lignes, 'nombre': len(lignes), 'ecritures': 0, 'limites': list(LIMITES)}, ensure_ascii=False, indent=2))
        return 0
    print('Contrôle des échéances (lecture seule) : %d échéance(s) à revoir.' % len(lignes))
    for x in lignes:
        print('- dossier %s · %s · départ %s · échéance %s · statut %s · agenda %s' % (
            x['dossier'], x['regle'], x['depart'], x['echeance'] or 'sans date', x['statut'], x['agenda'] or 'non inscrite'))
        for c in x['constats']:
            print('    · ' + LIBELLES[c])
    print('Ce que ce contrôle ne prouve pas :')
    for limite in LIMITES:
        print('  ' + limite)
    return 0


if __name__ == '__main__':
    sys.exit(main())
