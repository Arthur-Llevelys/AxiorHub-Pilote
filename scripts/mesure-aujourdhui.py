#!/usr/bin/env python3
"""5.6.95 (lot aujourdhui, A06) : mesure de la page Aujourd'hui et de l'encadré « Actuellement » sur un cabinet fictif.

Poste de développement seulement (jamais le serveur du cabinet) : dossier d'état temporaire, 300 dossiers fictifs DOS-001… (parties de la
liste des consignes), 5 000 courriels suivis, agenda, tâches, documents préparés, échéances, veille, travaux de l'agent. Aucun réseau, aucun
modèle (tout appel au modèle lève une erreur pendant la mesure).

Mesures : temps de calcul de l'encadré (médiane, 95e centile), requêtes SQL par rafraîchissement (comptées par ``set_trace_callback``),
poids de la réponse de rafraîchissement (200 et 304), temps de la page Aujourd'hui avec l'encadré masqué (« avant ») et affiché (« après »).

Usage : ``python scripts/mesure-aujourdhui.py [--dossiers 300] [--courriels 5000] [--repetitions 15] [--sortie rapport.json]``.
"""
import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import statistics
import sys
import tempfile
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
NOMS = ('DURAND', 'MARTIN', 'BERNARD', 'ZORVAL', 'KERVILAC', 'ORLANDEC')
OBJETS = ('Demande de pièces complémentaires', 'Calendrier de procédure', 'Relance sur la facture', 'Proposition transactionnelle', 'Convocation à l’audience',
          'Questions sur le bail commercial', 'Projet de protocole', 'Rendez-vous au cabinet')


def _iso(moment):
    return moment.astimezone(timezone.utc).isoformat()


def matters(n):
    return [{'id': 'DOS-%03d' % (i + 1), 'client_name': '%s %d' % (NOMS[i % 6], i + 1), 'path': '/Dossiers/DOS-%03d' % (i + 1), 'references': ['DOS-%03d' % (i + 1)],
             'aliases': [], 'correspondents': []} for i in range(n)]


def schemas(desk):
    from agent import workplan, echeances450, veille5632, rappels5688, crm5688, taches5688, livrables5689, file5651, modele5651, memoire5651
    for f in (workplan.ensure_schema, echeances450.ensure_schema, veille5632.schema, rappels5688.schema, crm5688.ensure_schema, taches5688.schema,
              livrables5689.ensure_schema, file5651.ensure_schema, modele5651.ensure_schema, memoire5651.ensure_schema):
        f(desk)


def evenement(desk, ident, titre, debut, minutes=60, matter=''):
    desk.db.execute('INSERT INTO calendar_cache(id,uid,recurrence_id,calendar_url,href,etag,title,description,location,starts,ends,busy,matter,matter_candidates,fetched) '
                    'VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)', (ident, ident, '', '/cal/', '/cal/' + ident, 'e', titre, '', '', _iso(debut), _iso(debut + timedelta(minutes=minutes)), 1, matter, '[]', _iso(debut)))


def tache(desk, ident, titre, due, matter='', statut='todo'):
    now = _iso(datetime.now(timezone.utc))
    desk.db.execute('INSERT INTO work_tasks_v211(id,external_uid,calendar_url,href,etag,matter,title,description,starts,due,duration_minutes,priority,status,blocked_by,action_type,act_type,'
                    'mail_key,source,created,updated,synced) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                    (ident, 'axiorhub-' + ident, '/t/', '/t/' + ident, 'e', matter, titre, '', '', due, 30, 5, statut, '', '', '', '', 'banc', now, now, now))


def courriel(desk, cle, sujet, matter='', etat='needs_action', recu=None):
    recu = recu or datetime.now(timezone.utc)
    desk.db.execute('INSERT INTO work_items(mail_key,state,source_status,source_reason,matter,subject,sender,received,updated,resolved) VALUES(?,?,?,?,?,?,?,?,?,?)',
                    (cle, etat, 'review', '', matter, sujet, 'contact@example.test', _iso(recu), _iso(recu), ''))


def prepare(desk, ident, titre, kind='contrat', matter='DOS-001', depot='', jours=1):
    when = _iso(datetime.now(timezone.utc) - timedelta(days=jours))
    desk.db.execute('INSERT INTO prepared_items5689(id,document_id,version_id,matter_id,kind,title,path,source_service,source_object_id,source_version,production_state,review_state,'
                    'deposit_state,source_at,created,updated) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                    (ident, 'doc-' + ident, 'v1', matter, kind, titre, '/Dossiers/%s/PROJETS/%s.docx' % (matter, ident), 'essai', ident, '1',
                     'preparing' if kind == 'courriel' else 'verified', 'to_review', depot, when, when, when))


def echeance(desk, ident, matter, due, statut='confirmee'):
    desk.db.execute("INSERT INTO deadlines450(id,matter,rule_id,start_event,start_date,due,status,origin,created,updated) VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (ident, matter, 'regle-fictive', 'signification', due, due, statut, 'banc', due, due))


def veille(desk, ident, titre, owner='cabinet'):
    desk.db.execute("INSERT INTO veille_items5632(id,owner,theme,kind,source_kind,source,url,title,published,decided,consulted,summary,reason,content_sha256,status,run_id) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (ident, owner, 'bail', 'decision', 'officiel', 'Source fictive', '', titre, '', '', _iso(datetime.now(timezone.utc)), '', '', ident, 'new', 1))


def travail(desk, kind, matter, statut):
    desk.db.execute('INSERT INTO jobs(kind,args,status,created) VALUES(?,?,?,?)', (kind, json.dumps({'matter': matter}), statut, _iso(datetime.now(timezone.utc))))


def semer(desk, liste, courriels=5000):
    """Cabinet fictif : quantités proportionnelles au nombre de dossiers ; ``courriels`` lignes de suivi (10 % à traiter)."""
    schemas(desk)
    now = datetime.now(timezone.utc)
    ids = [m['id'] for m in liste]
    n = len(ids)
    for i, mid in enumerate(ids):
        evenement(desk, 'ev-%d-a' % i, 'Audience fictive %d' % i, now + timedelta(hours=(i % 96) - 24), matter=mid)
        evenement(desk, 'ev-%d-b' % i, 'Rendez-vous fictif %d' % i, now + timedelta(days=(i % 20) - 10), matter=mid)
        tache(desk, 'tk-%d' % i, 'Tâche fictive %d : relire les pièces du dossier %s' % (i, mid), (now + timedelta(days=(i % 30) - 10)).date().isoformat(), mid)
        echeance(desk, 'dl-%d' % i, mid, (now + timedelta(days=(i % 60) - 5)).date().isoformat())
        for k in range(2):
            prepare(desk, 'pr-%d-%d' % (i, k), 'Projet fictif %d-%d' % (i, k), 'courriel' if k else 'contrat', mid, 'a_decider' if k and i % 3 == 0 else '', (i + k) % 45)
        if i % 5 == 0:
            desk.db.execute("INSERT OR IGNORE INTO d51_documents(matter,path,name,updated) VALUES(?,?,?,?)", (mid, '/Dossiers/%s/piece-%d.pdf' % (mid, i), 'piece-%d.pdf' % i, _iso(now)))
        if i % 25 == 0:
            desk.db.execute("INSERT INTO d51_changes(matter,dedup,kind,object_kind,object_id,version,title,detail,created) VALUES(?,?,?,?,?,?,?,?,?)",
                            (mid, 'mt-%d' % i, 'montant_modifie', 'assertion', 'a-%d' % i, '1', 'Montant fictif modifié dans le dossier %s' % mid, '{}', _iso(now)))
    for j in range(courriels):
        etat = 'needs_action' if j % 10 == 0 else ('draft_ready' if j % 10 == 1 else 'handled')
        courriel(desk, 'mail-%05d' % j, OBJETS[j % len(OBJETS)], ids[j % n], etat, now - timedelta(minutes=j * 7))
    for j in range(max(5, n // 10)):
        veille(desk, 'vj-%d' % j, 'Décision fictive %d sur le bail commercial' % j)
    for j in range(200):
        travail(desk, 'extract_facts460', ids[j % n], 'done')
    travail(desk, 'extract_facts460', ids[0], 'running')
    for j in range(20):
        travail(desk, 'analyze_deadline450', ids[j % n], 'pending')
    desk.db.commit()


def _compter(desk):
    compte = [0]

    def trace(sql):
        if sql.lstrip()[:6].upper() in ('SELECT', 'INSERT', 'UPDATE', 'DELETE', 'CREATE', 'PRAGMA', 'WITH S'):
            compte[0] += 1
    desk.db.set_trace_callback(trace)
    return compte


def mesurer_encadre(desk, repetitions=15, owner='cabinet', role='administrateur'):
    from agent import actuellement5695 as act
    with patch('agent.model.Model.ask', side_effect=AssertionError('modèle interdit')), patch('agent.model.Model.complete', side_effect=AssertionError('modèle interdit')):
        act.calculer(desk, owner, role, frozenset(), '/p')              # chauffe (imports, cache du système de fichiers)
        durees = []
        for _ in range(repetitions):
            t0 = time.perf_counter()
            resume = act.calculer(desk, owner, role, frozenset(), '/p')
            durees.append((time.perf_counter() - t0) * 1000)
        compte = _compter(desk)
        resume = act.calculer(desk, owner, role, frozenset(), '/p')
        desk.db.set_trace_callback(None)
        froid = []                                                     # sans la mémoire des points « À décider » (premier calcul, ou toutes les 5 min)
        for _ in range(max(3, repetitions // 3)):
            act._CACHE_STRATEGIE.clear()
            t0 = time.perf_counter()
            act.calculer(desk, owner, role, frozenset(), '/p')
            froid.append((time.perf_counter() - t0) * 1000)
        act._CACHE_STRATEGIE.clear()
        compte_froid = _compter(desk)
        act.calculer(desk, owner, role, frozenset(), '/p')
        desk.db.set_trace_callback(None)
    corps = json.dumps({**act.zone_json(resume), 'auto': True, 'calcul_ms': resume['calcul_ms']}, ensure_ascii=False).encode('utf-8')
    durees.sort()
    return {'ms_mediane': round(statistics.median(durees), 1), 'ms_p95': round(durees[max(0, int(len(durees) * .95) - 1)], 1), 'ms_max': round(durees[-1], 1),
            'requetes': compte[0], 'sans_memoire_ms_mediane': round(statistics.median(froid), 1), 'sans_memoire_ms_max': round(max(froid), 1), 'sans_memoire_requetes': compte_froid[0],
            'poids_200_octets': len(corps), 'poids_304_octets': 0, 'rubriques': [{k: r[k] for k in ('id', 'nombre', 'etat')} for r in resume['rubriques']]}


def mesurer_page(desk, repetitions=5, owner='cabinet'):
    """Page Aujourd'hui telle que la route la compose (file « À décider / À relire » comprise), encadré masqué puis affiché."""
    from agent import cockpit530, tableau5688
    from agent.file5651 import html as queue5651

    def page():
        queue = queue5651(desk, '/p', None, '', frozenset(), 8, actor=owner, role='administrateur')
        return cockpit530.page(desk, '/p', owner=owner, queue=queue)
    out = {}
    with patch('agent.model.Model.ask', side_effect=AssertionError('modèle interdit')), patch('agent.model.Model.complete', side_effect=AssertionError('modèle interdit')):
        for nom, masques in (('avant', ['actuellement']), ('apres', [])):
            tableau5688.enregistrer(desk, owner, {'ordre': list(tableau5688.IDS), 'masques': masques, 'accent': ''})
            page()
            durees, taille = [], 0
            for _ in range(repetitions):
                t0 = time.perf_counter()
                html = page()
                durees.append((time.perf_counter() - t0) * 1000)
                taille = len(html.encode('utf-8'))
            compte = _compter(desk)
            page()
            desk.db.set_trace_callback(None)
            out[nom] = {'ms_mediane': round(statistics.median(durees), 1), 'ms_max': round(max(durees), 1), 'requetes': compte[0], 'octets': taille,
                        'encadre_present': 'id="ax95-actuellement"' in html}
        tableau5688.reinitialiser(desk, owner)
    return out


def main(argv=None):
    parser = argparse.ArgumentParser(description='Mesure d’Aujourd’hui et de l’encadré « Actuellement » (poste de développement, données fictives).')
    parser.add_argument('--dossiers', type=int, default=300)
    parser.add_argument('--courriels', type=int, default=5000)
    parser.add_argument('--repetitions', type=int, default=15)
    parser.add_argument('--sortie', default='')
    args = parser.parse_args(argv)
    from agent.common import private_json
    from agent.desk import Desk
    with tempfile.TemporaryDirectory(prefix='axh-mesure-aujourdhui-') as tmp:
        base = Path(tmp)
        (base / 'state').mkdir()
        liste = matters(args.dossiers)
        private_json(base / 'matters.json', liste)
        c = {'mode': 'drafts', 'state_dir': str(base / 'state'), 'matters_file': str(base / 'matters.json'), 'mail': {'own_addresses': ['cabinet@example.test']},
             'nextcloud': {'roots': ['/Dossiers']}, 'documents': {}, 'ollama': {'model': 'interdit'}, 'calendar': {'urls': [], 'timezone': 'Europe/Paris'}}
        desk = Desk(c)
        try:
            t0 = time.perf_counter()
            semer(desk, liste, args.courriels)
            rapport = {'cabinet': {'dossiers': args.dossiers, 'courriels': args.courriels, 'semis_s': round(time.perf_counter() - t0, 1)},
                       'encadre': mesurer_encadre(desk, args.repetitions), 'page': mesurer_page(desk, max(3, args.repetitions // 3)),
                       'mesure_le': datetime.now(timezone.utc).isoformat(timespec='seconds'), 'python': sys.version.split()[0]}
        finally:
            desk.db.close()
    texte = json.dumps(rapport, ensure_ascii=False, indent=1)
    if args.sortie:
        Path(args.sortie).write_text(texte, encoding='utf-8')
    print(texte)
    return 0


if __name__ == '__main__':
    sys.exit(main())
