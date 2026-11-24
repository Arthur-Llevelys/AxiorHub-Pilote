"""5.6.24 (F25, F26, F30, F32, F34) : dossier central — rubriques par dossier, analyse suivie étape par étape, fraîcheur des résultats.

- Une commande unique « Analyser le dossier » (travail ``analyser_dossier5624``) enchaîne indexation, recherche des faits (toutes les
  pièces, par pages), projection de la chronologie et synthèse. Chaque étape enregistre son état (en cours, ok, erreur, ignorée), la
  révision des sources consommée, ses compteurs et sa dernière réussite ; une étape en erreur donne un état partiel explicite, les
  autres continuent quand elles n'en dépendent pas. Les demandes identiques sont regroupées (un seul travail en file par dossier).
- La révision courante d'un dossier combine les pièces indexées (chemin, version, date), les courriels indexés et les décisions
  humaines sur les faits ; un résultat dont la révision consommée diffère est affiché « périmé », jamais « à jour » sur la seule
  existence d'une projection.
- Douze rubriques (synthèse, parties, juridiction et procédure, rappel des faits, chronologie, prétentions et arguments, pièces, textes
  et jurisprudence, écritures et projets, simulation, décisions, échéances et actions) lisent les registres existants (mémoire de
  dossier validée, fiche synthétique, échéances, pièces indexées, autorités vérifiées, missions, matrice de preuves, analyses
  stratégiques). Chaque rubrique indique données présentes, analyse en cours, réserve ou action nécessaire ; rien n'est inventé.
"""
from datetime import date, datetime
import hashlib
from html import escape as e
import json
from pathlib import PurePosixPath
import re
import sqlite3
from urllib.parse import urlencode

from .common import Stop, load_matters

SCHEMA = '''CREATE TABLE IF NOT EXISTS analyses5624(
 matter TEXT NOT NULL, step TEXT NOT NULL, state TEXT NOT NULL, revision TEXT NOT NULL DEFAULT '', counters TEXT NOT NULL DEFAULT '{}',
 error TEXT NOT NULL DEFAULT '', started TEXT NOT NULL DEFAULT '', finished TEXT NOT NULL DEFAULT '', PRIMARY KEY(matter,step));'''
# 5.6.109 (C04) : couverture réelle. Chaque étape garde sa révision d'ENTRÉE (figée avant la lecture, jamais antidatée) et sa campagne ; une
# campagne est une demande d'analyse (une seule active par dossier : clé métier des demandes simultanées) ; la couverture garde l'état de
# chaque pièce et de chaque courriel éligible à sa version (traité, en attente, en erreur, exclu, avec le motif), ce que le repérage des faits
# a lu et ce que le modèle a réellement reçu pour la synthèse (une indexation n'est pas une analyse par le modèle).
COLONNES_56109 = (('entree', "TEXT NOT NULL DEFAULT ''"), ('campagne', 'INTEGER NOT NULL DEFAULT 0'))
SCHEMA_56109 = '''
CREATE TABLE IF NOT EXISTS analyses5624_campagnes(matter TEXT NOT NULL, numero INTEGER NOT NULL, mode TEXT NOT NULL DEFAULT 'complete',
 etat TEXT NOT NULL DEFAULT 'demandee', job_id INTEGER NOT NULL DEFAULT 0, demande_par TEXT NOT NULL DEFAULT '', cle TEXT NOT NULL DEFAULT '',
 etapes TEXT NOT NULL DEFAULT '[]', entrees TEXT NOT NULL DEFAULT '{}', instantane TEXT NOT NULL DEFAULT '{}', bilan TEXT NOT NULL DEFAULT '{}',
 cree_le TEXT NOT NULL, maj_le TEXT NOT NULL, fini_le TEXT NOT NULL DEFAULT '', PRIMARY KEY(matter, numero));
CREATE UNIQUE INDEX IF NOT EXISTS analyses5624_campagne_active ON analyses5624_campagnes(matter) WHERE etat IN ('demandee','en_cours','suspendue');
CREATE TABLE IF NOT EXISTS analyses5624_couverture(matter TEXT NOT NULL, genre TEXT NOT NULL, ref TEXT NOT NULL, nom TEXT NOT NULL DEFAULT '',
 version TEXT NOT NULL DEFAULT '', etat TEXT NOT NULL, motif TEXT NOT NULL DEFAULT '', partiel INTEGER NOT NULL DEFAULT 0,
 fragments INTEGER NOT NULL DEFAULT 0, lu_faits TEXT NOT NULL DEFAULT '', lu_modele TEXT NOT NULL DEFAULT '', campagne INTEGER NOT NULL DEFAULT 0,
 maj_le TEXT NOT NULL, PRIMARY KEY(matter, genre, ref));
CREATE INDEX IF NOT EXISTS analyses5624_couverture_etat ON analyses5624_couverture(matter, genre, etat);
'''
STEPS = (('indexation', 'Indexation des pièces et courriels'), ('faits', 'Recherche des faits dans les pièces'),
         ('chronologie', 'Chronologie du dossier'), ('synthese', 'Synthèse du dossier (modèle)'))
RUBRIQUES = (('synthese', 'Synthèse'), ('parties', 'Parties'), ('faits', 'Faits et procédure'), ('pretentions', 'Arguments des parties'),
             ('chronologie', 'Chronologie'), ('procedure', 'Juridiction et échéances'), ('pieces', 'Pièces'), ('textes', 'Textes et jurisprudence'),
             ('ecritures', 'Écritures et projets'), ('simulation', 'Simulation'), ('decisions', 'Décisions'), ('actions', 'Actions'),
             ('structure', 'Dossier structuré'), ('conseil', 'Conseil'))   # 5.6.89 : mémoire typée, mission de consultation, revue de contrat, accords
STATE_LABELS = {'jamais': 'jamais lancée', 'en_cours': 'en cours', 'ok': 'terminée', 'erreur': 'en erreur', 'ignoree': 'ignorée', 'a_jour': 'à jour',
                'perimee': 'périmée (sources ou faits modifiés depuis)', 'partielle': 'partielle',
                'interrompue': 'interrompue (arrêt, pause ou attente)'}   # 5.6.109 (C05) : jamais « en cours » sans travail, jamais « terminée »
LOTS_INDEXATION = 10      # lots d'indexation par passage du travail : une borne de travail, jamais une preuve de fin (C04)
MODES = {'changements': 'Actualiser ce qui a changé', 'complete': 'Analyse complète'}
CAMPAGNES_ACTIVES = ('demandee', 'en_cours', 'suspendue')
ETATS_CAMPAGNE = {'demandee': 'demandée', 'en_cours': 'en cours', 'suspendue': 'suspendue (reprise possible)', 'terminee': 'terminée',
                  'partielle': 'partielle', 'annulee': 'annulée', 'interrompue': 'interrompue'}
ETATS_COUVERTURE = {'traite': 'lu', 'en_attente': 'en attente', 'en_erreur': 'à vérifier', 'exclu': 'exclu'}
MOTIFS_COUVERTURE = {
    'aucun_texte_lisible': 'aucun texte lisible (scan sans texte ou page vide)',
    'texte_non_recupere': 'courriel rattaché au dossier dont le texte n’a pas été récupéré',
    'representation_secondaire_de_correspondance': 'copie de lecture d’un courriel déjà conservé',
    'piece_trop_volumineuse': 'fichier trop volumineux pour être lu',
    'archive_decompressee_trop_grosse': 'archive trop volumineuse une fois décompressée',
    'lecture_partielle': 'document long : seule une partie a été indexée',
}
EXCLUSIONS = ('representation_secondaire_de_correspondance', 'piece_trop_volumineuse', 'archive_decompressee_trop_grosse')
ANNULATIONS = frozenset(('operation_annulee', 'mission_annulee'))


def ensure_schema(desk):
    """Tables de l'analyse (démarrage, puis filet de sécurité des appels). Sans changement : aucune écriture, aucune validation (R05)."""
    voulus = re.findall(r'CREATE\s+(?:UNIQUE\s+)?(?:TABLE|INDEX)\s+IF\s+NOT\s+EXISTS\s+(\w+)', SCHEMA + SCHEMA_56109)
    existants = {r[0] for r in desk.db.execute("SELECT name FROM sqlite_master WHERE type IN ('table','index')")}
    if any(n not in existants for n in voulus):
        if desk.db.in_transaction:
            desk.db.commit()
        desk.db.executescript(SCHEMA + SCHEMA_56109)
        desk.db.commit()
    colonnes = {r[1] for r in desk.db.execute('PRAGMA table_info(analyses5624)')}
    manquantes = [(n, d) for n, d in COLONNES_56109 if n not in colonnes]
    for nom, definition in manquantes:
        desk.db.execute('ALTER TABLE analyses5624 ADD COLUMN %s %s' % (nom, definition))
    if manquantes:
        desk.db.commit()


def _matter(desk, mid):
    m = next((x for x in load_matters(desk.c) if str(x['id']) == str(mid)), None)
    if not m:
        raise Stop('dossier_absent')
    return m


def interruption(ex):
    """5.6.109 (C05) : arrêt central (annulation, pause, horaires, attente de budget ou d'abonnement, droits ou compte retirés, autre poste
    exécuteur). Il remonte tel quel au moteur des travaux : jamais transformé en « étape en erreur », en « partial » ni en reprise."""
    from .reprise56109 import arret_central
    return arret_central(ex)


def _lire_index(desk, mid):
    """Pièces et courriels indexés du dossier, pour les révisions : deux textes, ou l'exception de lecture (jamais « rien » par défaut)."""
    try:
        from .index import DocumentIndex
        idx = DocumentIndex(desk.c['state_dir'])
        try:
            return [json.dumps([list(r) for r in idx.db.execute('SELECT path,etag,modified FROM docs WHERE matter=? ORDER BY path', (mid,))], default=str),
                    str(tuple(idx.db.execute('SELECT COUNT(*),MAX(modified) FROM knowledge_chunks WHERE matter=?', (mid,)).fetchone()))]
        finally:
            idx.db.close()
    except Exception as ex:   # noqa: BLE001 — index illisible : révision distincte, donc jamais « à jour » par défaut
        return ex


def source_revision(desk, mid, lues=None):
    """Révision courante : pièces indexées (chemin, version, date), courriels indexés, décisions humaines sur les faits (F30)."""
    lues = _lire_index(desk, mid) if lues is None else lues
    parts = ['index_indisponible:' + str(lues)[:40]] if isinstance(lues, Exception) else list(lues)
    try:
        from .legal_memory import ensure_schema as lm_schema
        lm_schema(desk)
        parts.append(str(tuple(desk.db.execute('SELECT COUNT(*),MAX(updated),SUM(revision) FROM legal_memory_records WHERE matter=?', (mid,)).fetchone())))
    except Exception:
        pass
    try:   # 5.6.89 : une correction, une version ou un retrait de source rend la synthèse périmée
        parts.append(str(tuple(desk.db.execute('SELECT COUNT(*),MAX(updated),SUM(revision) FROM case_assertions5689 WHERE matter=?', (mid,)).fetchone())))
        parts.append(str(tuple(desk.db.execute("SELECT COUNT(*),SUM(etat='courante') FROM source_versions5689 WHERE matter=?", (mid,)).fetchone())))
    except Exception:
        pass
    return hashlib.sha256('|'.join(parts).encode()).hexdigest()[:16]


def revision_entrees(desk, mid, lues=None):
    """5.6.109 (C04) : révisions d'entrée séparées (lecture seule). ``sources`` : pièces et courriels indexés (chemin, version, date) ;
    ``decisions`` : décisions humaines (faits validés, épinglés, refusés ou archivés, faits de la fiche validés, dossier structuré). Un fait
    seulement proposé par une étape ne périme ni l'indexation ni l'étape qui l'a produit (avant : une seule révision mêlait les deux)."""
    def signe(parts):
        return hashlib.sha256('|'.join(parts).encode()).hexdigest()[:16]
    lues = _lire_index(desk, mid) if lues is None else lues
    sources, decisions = (['index_indisponible:' + type(lues).__name__] if isinstance(lues, Exception) else list(lues)), []
    for sql in ("SELECT id,status,revision FROM legal_memory_records WHERE matter=? AND status IN ('validated','pinned','disputed','archived') ORDER BY id",
                "SELECT id,status,updated FROM case_facts WHERE matter=? AND status<>'suggested' ORDER BY id",
                'SELECT COUNT(*),MAX(updated),SUM(revision) FROM case_assertions5689 WHERE matter=?',
                "SELECT COUNT(*),SUM(etat='courante') FROM source_versions5689 WHERE matter=?"):
        try:
            decisions.append(str([tuple(r) for r in desk.db.execute(sql, (mid,))]))
        except sqlite3.Error:   # table d'un module jamais initialisé : aucune décision de ce genre
            decisions.append('')
    return {'sources': signe(sources), 'decisions': signe(decisions)}


def _entree(step, entrees):
    """Ce que l'étape consomme : l'indexation lit le stockage (rien de local ne la périme) ; les faits lisent les sources ; la chronologie et
    la synthèse lisent les sources et les décisions humaines."""
    if step == 'indexation':
        return 'stockage'
    if step == 'faits':
        return 's:' + entrees['sources']
    return 's:%s|d:%s' % (entrees['sources'], entrees['decisions'])


def _step(desk, mid, step, state, revision='', counters=None, error='', entree=None, campagne=None):
    now = desk.now()
    desk.db.execute('INSERT OR IGNORE INTO analyses5624(matter,step,state,revision,counters,error,started,finished) VALUES(?,?,?,?,?,?,?,?)',
                    (mid, step, state, '', '{}', '', now, ''))
    if state == 'en_cours':
        desk.db.execute("UPDATE analyses5624 SET state='en_cours',error='',started=?,finished='' WHERE matter=? AND step=?", (now, mid, step))
    else:
        desk.db.execute('UPDATE analyses5624 SET state=?,revision=?,counters=?,error=?,finished=? WHERE matter=? AND step=?',
                        (state, revision, json.dumps(counters or {}, ensure_ascii=False, default=str), str(error or '')[:300], now, mid, step))
        if entree is not None:
            desk.db.execute('UPDATE analyses5624 SET entree=? WHERE matter=? AND step=?', (str(entree), mid, step))
    if campagne is not None:
        desk.db.execute('UPDATE analyses5624 SET campagne=? WHERE matter=? AND step=?', (int(campagne), mid, step))
    desk.db.commit()


def _pending_job(desk, mid, sauf=None):
    try:
        return desk.db.execute("SELECT id,status FROM jobs WHERE kind='analyser_dossier5624' AND status IN ('pending','running','cancel_requested') AND json_extract(args,'$.matter')=? AND id<>? ORDER BY id DESC LIMIT 1", (mid, int(sauf or 0))).fetchone()
    except Exception:
        return None


def _waiting_job(desk, mid):
    """5.6.109 : demande conservée mais qui n'avance pas (suspendue par une pause, ou en file derrière un plafond ou une limite) ; None sinon."""
    try:
        row = desk.db.execute("SELECT id,status,stop_reason,blocked_by FROM jobs WHERE kind='analyser_dossier5624' AND json_extract(args,'$.matter')=? "
                              "AND (status='suspended' OR (status='pending' AND COALESCE(blocked_by,'')<>'')) ORDER BY id DESC LIMIT 1", (mid,)).fetchone()
    except Exception:   # noqa: BLE001 — colonnes d'une base antérieure : pas d'attente connue
        return None
    if not row:
        return None
    motif = str(row['stop_reason'] or 'pause') if row['status'] == 'suspended' else str(row['blocked_by'] or '')
    libelles = {'pause': 'IA en pause : la demande est conservée et reprendra à la reprise de l’IA',
                'pause_dossier': 'IA en pause sur ce dossier : la demande est conservée et reprendra à la levée de la pause',
                'pause_horaire': 'pause programmée de l’IA : la demande reprendra à la fin de la plage'}
    return {'job': row['id'], 'status': row['status'], 'motif': motif,
            'libelle': libelles.get(motif) or 'la demande attend (%s) : elle reprendra sans nouvelle demande' % motif}


# ------------------------------------------------------------------------------------------------ campagnes et couverture (5.6.109, C04)
def _campagne(desk, mid, numero=None, active=False):
    if active:
        row = desk.db.execute("SELECT * FROM analyses5624_campagnes WHERE matter=? AND etat IN ('demandee','en_cours','suspendue') ORDER BY numero DESC LIMIT 1",
                              (mid,)).fetchone()
    elif numero:
        row = desk.db.execute('SELECT * FROM analyses5624_campagnes WHERE matter=? AND numero=?', (mid, int(numero))).fetchone()
    else:
        row = desk.db.execute('SELECT * FROM analyses5624_campagnes WHERE matter=? ORDER BY numero DESC LIMIT 1', (mid,)).fetchone()
    return dict(row) if row else None


def _campagne_etat(desk, mid, numero, etat, bilan=None):
    if not numero:
        return
    now = desk.now()
    desk.db.execute('UPDATE analyses5624_campagnes SET etat=?, maj_le=?, fini_le=CASE WHEN ? THEN ? ELSE fini_le END WHERE matter=? AND numero=?',
                    (etat, now, int(etat not in CAMPAGNES_ACTIVES), now, mid, int(numero)))
    if bilan is not None:
        desk.db.execute('UPDATE analyses5624_campagnes SET bilan=? WHERE matter=? AND numero=?',
                        (json.dumps(bilan, ensure_ascii=False, default=str), mid, int(numero)))
    desk.db.commit()


def _campagne_ouvrir(desk, mid, args):
    """Campagne de ce passage : celle de la demande (une reprise retrouve la sienne), sinon la campagne active du dossier, sinon une nouvelle
    (demande d'une version antérieure, test, relance). Rend ``(numéro, mode)``."""
    job = int(getattr(desk, 'active_job_id', None) or 0)
    brut = args.get('campagne')
    row = _campagne(desk, mid, int(brut)) if str(brut or '').isdigit() else None
    if row is None or row['etat'] not in CAMPAGNES_ACTIVES:
        row = _campagne(desk, mid, active=True)
    now = desk.now()
    if row is None:
        mode = args.get('mode') if args.get('mode') in MODES else 'complete'
        numero = int(desk.db.execute('SELECT COALESCE(MAX(numero),0) FROM analyses5624_campagnes WHERE matter=?', (mid,)).fetchone()[0]) + 1
        try:
            desk.db.execute("INSERT INTO analyses5624_campagnes(matter,numero,mode,etat,job_id,etapes,entrees,cree_le,maj_le) VALUES(?,?,?,'en_cours',?,?,?,?,?)",
                            (mid, numero, mode, job, json.dumps(sorted(args.get('steps') or [])), json.dumps(revision_entrees(desk, mid)), now, now))
        except sqlite3.IntegrityError:   # une autre demande vient d'ouvrir la campagne du dossier : ce passage la rejoint
            desk.db.rollback()
            row = _campagne(desk, mid, active=True) or _campagne(desk, mid)
            numero, mode = row['numero'], row['mode']
    else:
        numero, mode = row['numero'], row['mode']
        desk.db.execute("UPDATE analyses5624_campagnes SET etat='en_cours', job_id=CASE WHEN ? THEN ? ELSE job_id END, maj_le=? WHERE matter=? AND numero=?",
                        (int(bool(job)), job, now, mid, numero))
    desk.db.commit()
    return numero, mode


def _relever_couverture(desk, mid, numero, inventaire_complet):
    """Après l'indexation (dans le travail, jamais pendant une lecture) : état de CHAQUE pièce et de CHAQUE courriel éligible, à sa version
    courante. Une version nouvelle remet à zéro « lu par les faits » et « lu par le modèle » ; une pièce disparue n'est retirée que lorsque
    l'inventaire du stockage est complet (un parcours borné n'est pas une preuve d'absence)."""
    from .index import DocumentIndex
    from .common import fold
    idx = DocumentIndex(desk.c['state_dir'])
    now = desk.now()
    partiels = {}
    for path, kind, meta, n, etag, modified in idx.db.execute(
            'SELECT path,kind,MAX(meta),COUNT(*),MAX(etag),MAX(modified) FROM knowledge_chunks WHERE matter=? GROUP BY source_id', (mid,)):
        try:
            m = json.loads(meta or '{}')
        except ValueError:
            m = {}
        m = m if isinstance(m, dict) else {}
        partiels[(kind, path)] = (int(m.get('coverage') == 'partial'), int(n), m, str(etag or modified or ''))
    lignes = []
    for path, etag, modified, error, longueur in idx.db.execute('SELECT path,etag,modified,error,LENGTH(text) FROM docs WHERE matter=? ORDER BY path', (mid,)):
        error = str(error or '').split(':')[0]
        partiel, fragments = partiels.get(('document', path), (0, 0, {}, ''))[:2]
        if error in EXCLUSIONS or error.startswith('format_a_convertir'):
            etat, motif = 'exclu', error
        elif error:
            etat, motif = 'en_erreur', error
        elif not longueur:
            etat, motif = 'en_erreur', 'aucun_texte_lisible'
        else:
            etat, motif = 'traite', ('lecture_partielle' if partiel else '')
        lignes.append(('piece', path, PurePosixPath(path).name, str(etag or modified or ''), etat, motif, partiel, fragments))
    indexes = {}
    idx.db.close()
    for (kind, path), (partiel, fragments, m, version) in sorted(partiels.items()):
        if kind not in ('email_received', 'email_sent', 'email_history'):
            continue
        sujet = str(m.get('subject') or '') or PurePosixPath(path).name
        lignes.append(('courriel', path, sujet[:300], version, 'traite', 'lecture_partielle' if partiel else '', partiel, fragments))
        if kind == 'email_received':
            cle = (fold(sujet), fold(str(m.get('sender') or '')))
            indexes[cle] = indexes.get(cle, 0) + 1
    try:   # courriels rattachés au dossier par le registre des courriels : ceux dont le texte n'est pas indexé restent signalés, un par un
        rattaches = desk.db.execute('SELECT mail_key,subject,sender,received FROM work_items WHERE matter=? ORDER BY received,mail_key LIMIT 5000', (mid,)).fetchall()
    except sqlite3.Error:
        rattaches = []
    for r in rattaches:
        cle = (fold(str(r['subject'] or '')), fold(str(r['sender'] or '')))
        if indexes.get(cle, 0) > 0:
            indexes[cle] -= 1
            continue
        lignes.append(('courriel', 'courriel:' + str(r['mail_key']), str(r['subject'] or 'sans objet')[:300], str(r['received'] or ''), 'en_erreur',
                       'texte_non_recupere', 0, 0))
    vus = set()
    for genre, ref, nom, version, etat, motif, partiel, fragments in lignes:
        vus.add((genre, ref))
        desk.db.execute(
            'INSERT INTO analyses5624_couverture(matter,genre,ref,nom,version,etat,motif,partiel,fragments,campagne,maj_le) VALUES(?,?,?,?,?,?,?,?,?,?,?) '
            'ON CONFLICT(matter,genre,ref) DO UPDATE SET nom=excluded.nom, etat=excluded.etat, motif=excluded.motif, partiel=excluded.partiel, '
            "fragments=excluded.fragments, campagne=excluded.campagne, maj_le=excluded.maj_le, "
            "lu_faits=CASE WHEN version=excluded.version THEN lu_faits ELSE '' END, lu_modele=CASE WHEN version=excluded.version THEN lu_modele ELSE '' END, "
            'version=excluded.version', (mid, genre, ref, nom, version, etat, motif, partiel, fragments, int(numero or 0), now))
    if inventaire_complet:
        for r in desk.db.execute('SELECT genre,ref FROM analyses5624_couverture WHERE matter=?', (mid,)).fetchall():
            if (r['genre'], r['ref']) not in vus:
                desk.db.execute('DELETE FROM analyses5624_couverture WHERE matter=? AND genre=? AND ref=?', (mid, r['genre'], r['ref']))
    desk.db.commit()
    return couverture(desk, mid, inventaire_complet)


def _marquer_faits(desk, mid):
    """Le repérage des faits lit toutes les pièces indexées sans erreur (``facts460.scan_matter``) : chacune est notée à sa version."""
    desk.db.execute("UPDATE analyses5624_couverture SET lu_faits=version WHERE matter=? AND genre='piece' AND etat='traite'", (mid,))
    desk.db.commit()


def _marquer_modele(desk, mid):
    """Ce que le modèle a réellement reçu pour la synthèse : les sources enregistrées avec la fiche (des extraits choisis, pas tout le dossier)."""
    row = desk.db.execute('SELECT sources FROM case_briefs WHERE matter=? ORDER BY version DESC LIMIT 1', (mid,)).fetchone()
    try:
        chemins = sorted({str(s.get('path') or '') for s in json.loads(row['sources'] or '[]') if isinstance(s, dict)} - {''}) if row else []
    except (ValueError, TypeError):
        chemins = []
    for chemin in chemins:
        desk.db.execute('UPDATE analyses5624_couverture SET lu_modele=version WHERE matter=? AND ref=?', (mid, chemin))
    desk.db.commit()
    return len(chemins)


def couverture(desk, mid, inventaire_complet=None):
    """Couverture conservée du dossier (lecture seule) : compteurs par genre et par état, ce qui a été lu par le repérage des faits et par le
    modèle, motifs de ce qui n'est pas couvert. ``satisfaite`` : couverture définie, inventaire complet, rien en attente ; les éléments
    illisibles ou exclus restent des limites affichées avec leur motif (ils ne se résolvent pas en relançant l'analyse)."""
    vide = lambda: {'total': 0, 'traite': 0, 'en_attente': 0, 'en_erreur': 0, 'exclu': 0, 'partiels': 0, 'faits': 0, 'modele': 0}
    out = {'definie': False, 'campagne': 0, 'inventaire_complet': inventaire_complet, 'satisfaite': False, 'pieces': vide(), 'courriels': vide(),
           'motifs': [], 'reserves': 0}
    try:
        rows = desk.db.execute("SELECT genre,etat,motif,COUNT(*) n,SUM(partiel) p,SUM(lu_faits<>'' AND lu_faits=version) f,"
                               "SUM(lu_modele<>'' AND lu_modele=version) m,MAX(campagne) c FROM analyses5624_couverture WHERE matter=? "
                               'GROUP BY genre,etat,motif ORDER BY genre,etat,motif', (mid,)).fetchall()
        etape = desk.db.execute("SELECT state,counters,campagne FROM analyses5624 WHERE matter=? AND step='indexation'", (mid,)).fetchone()
    except sqlite3.Error:
        return out
    for r in rows:
        g = out['pieces' if r['genre'] == 'piece' else 'courriels']
        g['total'] += r['n']
        g[r['etat']] = g.get(r['etat'], 0) + r['n']
        g['partiels'] += r['p'] or 0
        g['faits'] += r['f'] or 0
        g['modele'] += r['m'] or 0
        out['campagne'] = max(out['campagne'], r['c'] or 0)
        if r['etat'] != 'traite' or r['motif']:
            out['motifs'].append({'genre': r['genre'], 'etat': r['etat'], 'motif': r['motif'], 'nombre': r['n'],
                                  'libelle': MOTIFS_COUVERTURE.get(r['motif']) or motif_lisible(r['motif'])})
    if inventaire_complet is None and etape is not None:
        try:
            compteurs = json.loads(etape['counters'] or '{}')
        except ValueError:
            compteurs = {}
        if 'inventaire_complet' in compteurs:
            out['inventaire_complet'] = bool(compteurs['inventaire_complet'])
        out['definie'] = bool(etape['campagne']) and 'inventaire_complet' in compteurs
    elif inventaire_complet is not None:
        out['definie'] = True
    out['reserves'] = sum(g['en_erreur'] + g['partiels'] for g in (out['pieces'], out['courriels']))
    out['satisfaite'] = bool(out['definie'] and out['inventaire_complet'] and not out['pieces']['en_attente'] and not out['courriels']['en_attente'])
    return out


def motif_lisible(code):
    code = str(code or '')
    if code.startswith('format_a_convertir'):
        return 'format non pris en charge (à convertir en PDF ou en Word)'
    if not code:
        return ''
    try:
        from .erreurs5634 import explain
        return explain(code, None)
    except Exception:   # noqa: BLE001
        return code.replace('_', ' ')


def couverture_liste(desk, mid, genre='', etat='', limite=200, apres=''):
    """Éléments de la couverture (lecture seule, bornée, paginée par ``apres``) : une ligne par pièce ou par courriel éligible."""
    sql, params = 'SELECT genre,ref,nom,version,etat,motif,partiel,fragments,lu_faits,lu_modele,campagne FROM analyses5624_couverture WHERE matter=?', [mid]
    if genre in ('piece', 'courriel'):
        sql += ' AND genre=?'
        params.append(genre)
    if etat in ETATS_COUVERTURE:
        sql += ' AND etat=?'
        params.append(etat)
    if apres:
        sql += ' AND ref>?'
        params.append(str(apres))
    limite = max(1, min(int(limite or 200), 500))
    try:
        rows = desk.db.execute(sql + ' ORDER BY ref LIMIT ?', params + [limite + 1]).fetchall()
    except sqlite3.Error:
        return {'elements': [], 'suite': ''}
    elements = [{'genre': r['genre'], 'ref': r['ref'], 'nom': r['nom'], 'etat': r['etat'], 'etat_libelle': ETATS_COUVERTURE.get(r['etat'], r['etat']),
                 'motif': r['motif'], 'motif_libelle': MOTIFS_COUVERTURE.get(r['motif']) or motif_lisible(r['motif']), 'partiel': bool(r['partiel']),
                 'faits': bool(r['lu_faits'] and r['lu_faits'] == r['version']), 'modele': bool(r['lu_modele'] and r['lu_modele'] == r['version'])}
                for r in rows[:limite]]
    return {'elements': elements, 'suite': rows[limite - 1]['ref'] if len(rows) > limite else ''}


def state(desk, mid, sans_travail=None):
    """État de l'analyse du dossier : étapes, révision consommée, fraîcheur, travail en file, dernière réussite, couverture (lecture seule).
    5.6.109 : « à jour » exige TOUTES les étapes terminées et fraîches ET une couverture définie et satisfaite ; une étape restée « en cours »
    sans travail actif (coupure, arrêt) est dite « interrompue » ; une demande suspendue est signalée avec son motif."""
    ensure_schema(desk)
    lues = _lire_index(desk, mid)
    current = source_revision(desk, mid, lues)
    entrees = revision_entrees(desk, mid, lues)
    rows = {r['step']: dict(r) for r in desk.db.execute('SELECT * FROM analyses5624 WHERE matter=?', (mid,))}
    pending = _pending_job(desk, mid, sans_travail)
    steps, last_ok = [], ''
    for key, label in STEPS:
        r = rows.get(key)
        if not r:
            steps.append({'step': key, 'label': label, 'state': 'jamais', 'state_label': STATE_LABELS['jamais'], 'fresh': None, 'counters': {}, 'error': '', 'finished': '',
                          'campagne': 0})
            continue
        etat = 'interrompue' if r['state'] == 'en_cours' and not pending and not sans_travail else r['state']   # C05 : conciliée à la lecture, sans écrire
        fresh = None
        if etat == 'ok':
            fresh = (r['entree'] == _entree(key, entrees)) if r['entree'] else (r['revision'] == current)
        if etat == 'ok' and r['finished'] > last_ok:
            last_ok = r['finished']
        steps.append({'step': key, 'label': label, 'state': etat, 'state_label': STATE_LABELS.get(etat, etat), 'fresh': fresh,
                      'counters': json.loads(r['counters'] or '{}'), 'error': r['error'], 'finished': r['finished'], 'revision': r['revision'],
                      'campagne': r['campagne']})
    couv = couverture(desk, mid)
    if pending:
        overall = 'en_cours'
    elif all(s['state'] == 'jamais' for s in steps):
        overall = 'jamais'
    elif any(s['state'] != 'ok' for s in steps):
        overall = 'partielle'
    elif couv['definie'] and not couv['satisfaite']:
        overall = 'partielle'
    elif any(s['fresh'] is False for s in steps):
        overall = 'perimee'
    else:
        overall = 'a_jour'
    label = STATE_LABELS.get(overall, overall)
    if overall == 'a_jour' and couv['reserves']:
        label += ', avec %d élément%s à vérifier' % (couv['reserves'], 's' if couv['reserves'] > 1 else '')
    camp = _campagne(desk, mid)
    return {'matter': mid, 'revision': current, 'steps': steps, 'job': pending['id'] if pending else None, 'overall': overall,
            'overall_label': label, 'last_success': last_ok, 'couverture': couv, 'attente': None if pending else _waiting_job(desk, mid),
            'arret_demande': bool(pending and pending['status'] == 'cancel_requested'),
            'campagne': {'numero': camp['numero'], 'mode': camp['mode'], 'etat': camp['etat'], 'etat_libelle': ETATS_CAMPAGNE.get(camp['etat'], camp['etat']),
                         'cree_le': camp['cree_le'], 'fini_le': camp['fini_le']} if camp else None}


def request_analysis(desk, mid, steps=None, mode='', par='', cle=''):
    """Demande regroupée : un seul travail en file par dossier (F25). 5.6.109 : une seule campagne active par dossier, créée avec son travail
    dans UNE transaction (clé métier : deux demandes simultanées retrouvent la même commande) ; un travail dont l'arrêt est demandé n'est
    pas doublé ; une demande suspendue par une pause encore en vigueur est retrouvée, pas recréée."""
    _matter(desk, mid)
    ensure_schema(desk)
    deja = _deja_demandee(desk, mid)
    if deja:
        return deja
    args = {'matter': mid}
    if steps:
        args['steps'] = [s for s in steps if s in dict(STEPS)]
    if mode in MODES:
        args['mode'] = mode
    if desk.db.in_transaction:
        desk.db.commit()
    desk.db.execute('BEGIN IMMEDIATE')
    try:
        deja = _deja_demandee(desk, mid)          # relu sous le verrou d'écriture : la seconde de deux requêtes simultanées s'arrête ici
        if deja:
            desk.db.rollback()
            return deja
        desk.db.execute("UPDATE analyses5624_campagnes SET etat='interrompue', maj_le=?, fini_le=? WHERE matter=? AND etat IN ('demandee','en_cours','suspendue')",
                        (desk.now(), desk.now(), mid))   # campagne restée active sans travail vivant (coupure) : fermée, jamais reprise en silence
        numero = int(desk.db.execute('SELECT COALESCE(MAX(numero),0) FROM analyses5624_campagnes WHERE matter=?', (mid,)).fetchone()[0]) + 1
        args['campagne'] = numero
        job = desk.enqueue('analyser_dossier5624', args, priority=20, transactional=True)
        now = desk.now()
        desk.db.execute("INSERT INTO analyses5624_campagnes(matter,numero,mode,etat,job_id,demande_par,cle,etapes,entrees,cree_le,maj_le) "
                        "VALUES(?,?,?,'demandee',?,?,?,?,?,?,?)", (mid, numero, args.get('mode') or 'complete', job, str(par or '')[:200], str(cle or '')[:120],
                                                                 json.dumps(args.get('steps') or []), json.dumps(revision_entrees(desk, mid)), now, now))
        desk.db.commit()
    except BaseException:
        if desk.db.in_transaction:
            desk.db.rollback()
        raise
    desk.notify_queued(job, args)
    return {'job_id': job, 'already': False, 'campagne': numero,
            'message': 'Analyse du dossier lancée (travail n° %d) : indexation, faits, chronologie, synthèse.' % job}


def _deja_demandee(desk, mid):
    """Commande déjà vivante pour ce dossier : travail en file ou en cours, arrêt en attente de confirmation, ou demande suspendue par une
    pause encore en vigueur. None : rien de vivant."""
    pending = _pending_job(desk, mid)
    if pending:
        if pending['status'] == 'cancel_requested':
            return {'job_id': pending['id'], 'already': True, 'arret_demande': True,
                    'message': 'L’arrêt de l’analyse (travail n° %d) attend encore sa confirmation : relancez ensuite.' % pending['id']}
        return {'job_id': pending['id'], 'already': True, 'message': 'Analyse déjà en file (travail n° %d) : indexation, faits, chronologie, synthèse.' % pending['id']}
    attente = _waiting_job(desk, mid)
    if attente and attente['status'] == 'suspended':
        try:
            from .arret5643 import pause_in_effect
            row = desk.db.execute('SELECT id,kind,origin,priority,args,status,stop_reason,matter_ref FROM jobs WHERE id=?', (attente['job'],)).fetchone()
            vivante = bool(row) and pause_in_effect(desk, row)
        except Exception:   # noqa: BLE001 — contrôle de la pause illisible : la demande existante est gardée (jamais une seconde par défaut)
            vivante = True
        if vivante:
            return {'job_id': attente['job'], 'already': True, 'en_attente': attente['motif'],
                    'message': 'Demande déjà enregistrée (travail n° %d) : %s.' % (attente['job'], attente['libelle'])}
    return None


def analyse(desk, args):
    """Travail « analyser_dossier5624 » : parcours complet et suivi ; une demande en file n'est jamais présentée comme une analyse terminée.
    5.6.109 : contrôle d'arrêt avant chaque étape et chaque lot ; un arrêt central remonte tel quel (C05), l'étape devient « interrompue »
    avec ce qui a déjà été accepté ; entrée de chaque étape figée AVANT sa lecture ; couverture relevée après l'indexation ; une limite de
    lots atteinte ou un inventaire incomplet donne une indexation « partielle », jamais « terminée » (C04)."""
    mid = str(args.get('matter') or '')
    _matter(desk, mid)
    ensure_schema(desk)
    wanted = set(args.get('steps') or [s for s, _ in STEPS])
    errors, inchangees = {}, []
    from .pause5629 import checkpoint
    checkpoint(desk)
    numero, mode = _campagne_ouvrir(desk, mid, args)
    avant = {r['step']: dict(r) for r in desk.db.execute('SELECT * FROM analyses5624 WHERE matter=?', (mid,))}

    def acquise(step):
        """Étape déjà terminée sur les MÊMES entrées : dans cette campagne (reprise après suspension), ou en mode « Actualiser ce qui a
        changé ». Elle n'est pas refaite (aucun nouvel appel au modèle pour une synthèse dont rien n'a changé)."""
        r = avant.get(step)
        if not r or r['state'] != 'ok' or not r['entree'] or (mode != 'changements' and r['campagne'] != numero):
            return False
        return r['entree'] == _entree(step, revision_entrees(desk, mid))

    def arreter(step, ex, counters=None):
        code = str(ex).split(':')[0]
        try:   # ce qui a déjà pu être accepté est conservé ; l'étape n'est plus « en cours », la campagne dit son état réel
            _step(desk, mid, step, 'interrompue', '', counters or {}, code, campagne=numero)
            _campagne_etat(desk, mid, numero, 'annulee' if code in ANNULATIONS else 'suspendue', {'arret': code, 'etape': step})
        except sqlite3.Error:
            pass   # base momentanément illisible : la lecture conciliera l'étape restée « en cours » (``state``)

    def controler(step):
        """Contrôle d'arrêt entre deux étapes : aucune étape n'est engagée après l'acquittement ; la campagne dit son état réel."""
        try:
            checkpoint(desk)
        except Stop as ex:
            code = str(ex).split(':')[0]
            try:
                _campagne_etat(desk, mid, numero, 'annulee' if code in ANNULATIONS else 'suspendue', {'arret': code, 'avant': step})
            except sqlite3.Error:
                pass
            raise

    if 'indexation' in wanted:
        _step(desk, mid, 'indexation', 'en_cours', campagne=numero)
        counters = {'lots': 0}
        try:
            fini = False
            for _ in range(LOTS_INDEXATION):
                checkpoint(desk)
                res = desk.perform('index', {'matter': mid}) or {}
                counters['lots'] += 1
                counters.update({k: res.get(k) for k in ('indexes', 'en_attente', 'contenu_modifie', 'fichiers') if k in res})
                inventaire = res.get('inventaire') if isinstance(res.get('inventaire'), dict) else {}
                if not res.get('en_attente') and inventaire.get('complete') is not False:
                    fini = True      # plus rien en attente ET inventaire du stockage parcouru jusqu'au bout
                    break
            counters['inventaire_complet'] = fini
            if not fini:
                counters['motif'] = 'limite de %d lots atteinte : des fichiers restent à indexer, relancez pour poursuivre' % LOTS_INDEXATION
            try:
                resume = _relever_couverture(desk, mid, numero, fini)
                counters['couverture'] = {'pieces': resume['pieces'], 'courriels': resume['courriels']}
            except (sqlite3.Error, OSError) as ex:   # la couverture ne se relève pas : l'étape ne peut pas être dite complète
                fini = False
                counters['inventaire_complet'] = False
                counters['motif'] = 'couverture non relevée (%s)' % type(ex).__name__
            _step(desk, mid, 'indexation', 'ok' if fini else 'partielle', source_revision(desk, mid), counters, entree='stockage', campagne=numero)
        except Exception as ex:   # pylint: disable=broad-except
            if interruption(ex):
                arreter('indexation', ex, counters)
                raise
            errors['indexation'] = str(ex)[:200]
            _step(desk, mid, 'indexation', 'erreur', '', {}, str(ex), campagne=numero)
    if 'faits' in wanted:
        controler('faits')
        if acquise('faits'):
            inchangees.append('faits')
        else:
            entree = _entree('faits', revision_entrees(desk, mid))
            _step(desk, mid, 'faits', 'en_cours', campagne=numero)
            try:
                from .facts460 import scan_matter
                res = scan_matter(desk, {'matter': mid, 'sync': False})
                _marquer_faits(desk, mid)
                _step(desk, mid, 'faits', 'ok', source_revision(desk, mid), {k: res.get(k) for k in ('documents', 'faits', 'nouveaux', 'modifies', 'inchanges', 'refus_respectes', 'erreurs', 'documents_peu_lisibles') if k in res},
                      entree=entree, campagne=numero)
            except Exception as ex:   # pylint: disable=broad-except
                if interruption(ex):
                    arreter('faits', ex)
                    raise
                errors['faits'] = str(ex)[:200]
                _step(desk, mid, 'faits', 'erreur', '', {}, str(ex), campagne=numero)
    if 'chronologie' in wanted:
        controler('chronologie')
        if acquise('chronologie'):
            inchangees.append('chronologie')
        else:
            entree = _entree('chronologie', revision_entrees(desk, mid))
            _step(desk, mid, 'chronologie', 'en_cours', campagne=numero)
            try:
                from .legal_memory import sync_matter_memory
                res = sync_matter_memory(desk, {'matter': mid})
                _step(desk, mid, 'chronologie', 'ok', source_revision(desk, mid), {'evenements': res['chronologie'], 'agenda': res['evenements_agenda'], 'agenda_erreur': res['agenda_erreur']},
                      entree=entree, campagne=numero)
            except Exception as ex:   # pylint: disable=broad-except
                if interruption(ex):
                    arreter('chronologie', ex)
                    raise
                errors['chronologie'] = str(ex)[:200]
                _step(desk, mid, 'chronologie', 'erreur', '', {}, str(ex), campagne=numero)
    if 'synthese' in wanted:
        controler('synthese')
        if 'indexation' in errors:
            _step(desk, mid, 'synthese', 'ignoree', '', {}, 'indexation en erreur : synthèse non recalculée', campagne=numero)
        elif acquise('synthese'):
            inchangees.append('synthese')
        else:
            entree = _entree('synthese', revision_entrees(desk, mid))
            _step(desk, mid, 'synthese', 'en_cours', campagne=numero)
            try:
                from .intelligence import refresh_brief
                res = refresh_brief(desk, {'matter': mid})
                compteurs = {'fiche_version': res.get('fiche_version'), 'sources': res.get('sources'), 'fichiers_en_attente': res.get('fichiers_en_attente')}
                try:
                    compteurs['sources_lues_par_le_modele'] = _marquer_modele(desk, mid)
                except sqlite3.Error:
                    pass
                _step(desk, mid, 'synthese', 'ok', source_revision(desk, mid), compteurs, entree=entree, campagne=numero)
            except Exception as ex:   # pylint: disable=broad-except
                if interruption(ex):
                    arreter('synthese', ex)
                    raise
                errors['synthese'] = str(ex)[:200]
                _step(desk, mid, 'synthese', 'erreur', '', {}, str(ex), campagne=numero)
    st = state(desk, mid, sans_travail=getattr(desk, 'active_job_id', None))
    desk.audit('dossier5624_analyse', {'matter': mid, 'etat': st['overall'], 'erreurs': sorted(errors), 'campagne': numero})
    done = [s['step'] for s in st['steps'] if s['state'] == 'ok']
    message = 'Analyse du dossier %s : %s.' % (STATE_LABELS.get(st['overall'], st['overall']), ', '.join(done) or 'aucune étape réussie')
    if errors:
        message += ' Étape(s) en erreur : ' + ' ; '.join('%s (%s)' % (k, v[:80]) for k, v in errors.items()) + '. Le bouton « Reprendre » relance ces seules étapes.'
    couv = st['couverture']
    complet = not errors and all(s['state'] == 'ok' for s in st['steps'] if s['step'] in wanted) and (
        'indexation' not in wanted or not couv['definie'] or couv['satisfaite'])
    if not complet and not errors:
        message += ' Analyse partielle : ce qui n’a pas été lu est listé dans la couverture du dossier.'
    try:   # 5.6.109 (F03) : ce que l'analyse changerait à une information VALIDÉE devient une proposition à décider, jamais un remplacement
        from .reanalyse56109 import apres_campagne
        a_decider = apres_campagne(desk, mid, numero)
    except Exception as ex:   # pylint: disable=broad-except
        if interruption(ex):
            raise
        a_decider = None    # la comparaison n'a pas pu être préparée : dit dans le bilan, l'analyse garde son résultat
    if a_decider:
        message += ' %d changement(s) proposé(s) sur des informations validées : à décider dans la fiche du dossier.' % a_decider
    _campagne_etat(desk, mid, numero, 'terminee' if complet else 'partielle',
                   {'etapes': {s['step']: s['state'] for s in st['steps']}, 'erreurs': errors, 'inchangees': inchangees, 'propositions': a_decider,
                    'couverture': {'pieces': couv['pieces'], 'courriels': couv['courriels'], 'satisfaite': couv['satisfaite']}})
    return {'status': 'prepared' if complet else 'partial', 'matter': mid, 'etat': st['overall'], 'steps': {s['step']: s['state'] for s in st['steps']},
            'errors': errors, 'message': message, 'campagne': numero, 'inchangees': inchangees, 'propositions': a_decider}


# ------------------------------------------------------------------------------------------------ rubriques
def _records(desk, mid):
    from .legal_memory import memory_records
    return memory_records(desk, mid, None, 500)


def _fact_view(desk, mid, r):
    from .fiche460 import _fact
    return _fact(desk, mid, r) | {'type': r['record_type']}


def _brief(desk, mid):
    try:
        row = desk.db.execute('SELECT version,data,created FROM case_briefs WHERE matter=? ORDER BY version DESC LIMIT 1', (mid,)).fetchone()
    except Exception:
        return None
    if not row:
        return None
    try:
        return {'version': row['version'], 'created': row['created'], 'data': json.loads(row['data'])}
    except ValueError:
        return None


def _docs(desk, mid, limit=500):
    try:
        from .index import DocumentIndex
        idx = DocumentIndex(desk.c['state_dir'])
        return [{'path': p, 'name': PurePosixPath(p).name, 'modified': str(mod or '')[:10], 'error': err or '', 'etag': etag or ''}
                for p, etag, mod, err in idx.db.execute('SELECT path,etag,modified,error FROM docs WHERE matter=? ORDER BY modified DESC LIMIT ?', (mid, limit))]
    except Exception:
        return []


def _mission_items(desk, mid, output_types, limit=40):
    out = []
    try:
        for a in desk.db.execute("SELECT a.output_type,a.content,a.created,m.title FROM artifacts5614 a JOIN missions5614 m ON m.id=a.mission "
                                 "WHERE m.matter=? AND a.state='actif' AND a.output_type IN (%s) ORDER BY a.created DESC LIMIT 12" % ','.join('?' * len(output_types)), (mid, *output_types)):
            content = json.loads(a['content'])
            items = content.get('items') if isinstance(content, dict) else None
            out.append({'type': a['output_type'], 'mission': a['title'], 'created': a['created'][:10], 'summary': (content.get('summary') if isinstance(content, dict) else '') or '',
                        'items': [i for i in (items or []) if isinstance(i, dict)][:limit], 'proposition': True})
    except Exception:
        pass
    return out


def rubrique(desk, mid, key):
    """Données d'une rubrique : ``state`` ∈ {present, vide, en_cours, reserve}, ``items``, ``note`` (ce qui manque ou l'action à faire)."""
    from . import fiche460
    st = state(desk, mid)
    busy = st['overall'] == 'en_cours'
    out = {'key': key, 'label': dict(RUBRIQUES).get(key, key), 'items': [], 'state': 'vide', 'note': '', 'analysis': st}
    if key in ('structure', 'conseil'):   # 5.6.89 : le contrôle de la requête a déjà écarté un dossier réservé ; il est refait ici sur le périmètre de la requête
        from .membres5650 import viewer_hidden
        if str(mid) in (viewer_hidden() or frozenset()):
            raise Stop('dossier_reserve')
        if key == 'structure':
            from . import assertions5689
            out['projection'] = assertions5689.projection(desk, mid, 'cabinet', 'administrateur')
        out['state'] = 'present'
        return out
    records = _records(desk, mid) if key in ('parties', 'faits', 'pretentions', 'decisions', 'procedure', 'synthese') else []
    active = [r for r in records if r['status'] in ('pinned', 'validated', 'suggested')]
    refused = [r for r in records if r['status'] == 'disputed']
    if key == 'parties':
        m = _matter(desk, mid)
        from . import dossier5627
        fiche = dossier5627.latest(desk, mid)
        if fiche and (fiche['data'].get('parties') or (fiche.get('edits') or {}).get('parties')):   # 5.6.27 : parties de la fiche de travail
            edit = (fiche.get('edits') or {}).get('parties')
            rows = ([{'nom': l.strip(), 'qualite': '', 'conseil': '', 'client_du_cabinet': False} for l in edit['text'].splitlines() if l.strip()] if edit
                    else fiche['data']['parties'])
            out['items'] = [{'id': '', 'value': p['nom'] + ((' — ' + dossier5627.QUALITES.get(p['qualite'], '')) if p.get('qualite') else '') +
                             ((' — conseil : ' + p['conseil']) if p.get('conseil') else ''), 'title': p['nom'],
                             'status': 'validated' if edit or p.get('client_du_cabinet') else 'derived',
                             'status_label': 'Corrigé par l’avocat' if edit else ('Client du cabinet' if p.get('client_du_cabinet') else 'Fiche de travail'),
                             'confidence': 'high', 'sources': [{'label': 'Fiche de travail v%d' % fiche['version'], 'href': '/dossier?id=' + mid, 'excerpt': ''}],
                             'validated': bool(edit), 'type': 'party'} for p in rows]
            from . import dossier5644   # 5.6.44 : corrections de l'avocat et doublons fusionnés
            out['items'] = dossier5644.apply(desk, mid, 'parties', out['items'])
            out['refused'] = 0
            out['state'] = 'present'
            out['analysis'] = st
            out['freshness'] = 'a_jour'
            return out
        from .facts460 import NOT_PARTY, party_name
        from .common import fold
        old = ('Personne citée dans la pièce', 'Partie citée dans la pièce')   # repérage d'avant 5.6.27 : tout nom cité, non fiable
        items = [_fact_view(desk, mid, r) for r in active if r['record_type'] == 'party'
                 and (r['status'] in ('validated', 'pinned') or not str(r.get('content') or '').startswith(old))]
        items = [i for i in items if party_name(str(i.get('value') or i.get('title') or '').replace('Partie : ', '')) and not NOT_PARTY.search(fold(str(i.get('value') or '')))]
        if m.get('client_name'):
            items.insert(0, {'id': '', 'value': m['client_name'], 'title': m['client_name'], 'status': 'configured', 'status_label': 'Configuration du dossier', 'confidence': 'high',
                             'sources': [{'label': 'Configuration du dossier', 'href': '', 'excerpt': ''}], 'validated': True, 'type': 'party'})
        brief = _brief(desk, mid)
        from .dossier5644 import head
        for p in ((brief or {}).get('data') or {}).get('parties', []) or []:
            if isinstance(p, str) and not any(p.lower() in str(i.get('value', '')).lower() or (head(p) and head(p) == head(i.get('value')))
                                              for i in items):
                items.append({'id': '', 'value': p, 'title': p, 'status': 'derived', 'status_label': 'Synthèse (non validé)', 'confidence': 'medium', 'sources': [{'label': 'Synthèse v%s' % brief['version'], 'href': '', 'excerpt': ''}], 'validated': False, 'type': 'party'})
        from . import dossier5644   # 5.6.44 : corrections de l'avocat et doublons fusionnés
        out['items'] = dossier5644.apply(desk, mid, 'parties', items)
        out['refused'] = len([r for r in refused if r['record_type'] == 'party'])
    elif key == 'procedure':
        from . import echeances450 as ech
        ech.ensure_schema(desk)
        from . import dossier5644
        for kind in ('jurisdiction', 'case_number'):
            best = fiche460._best(records, kind)
            shown = dossier5644.single(desk, mid, kind, _fact_view(desk, mid, best) if best else None)   # 5.6.44 : corrections de l'avocat
            if shown:
                out['items'].append({'kind': kind, 'label': 'Juridiction' if kind == 'jurisdiction' else 'Numéro de RG', **shown})
        acts = [_fact_view(desk, mid, r) for r in active if r['record_type'] in ('completed_action', 'deadline', 'planned_action')]
        acts.sort(key=lambda x: x.get('event_date') or '', reverse=True)
        out['actes'] = acts
        out['echeances'] = [{'label': d['rule_label'], 'due': d['due'], 'due_label': d['due_label'], 'status_label': d['status_label'], 'validated': d['status'] not in ('a_confirmer', 'a_completer'),
                             'href': '/echeances#e-' + d['id'], 'source': d['source_path'].rsplit('/', 1)[-1] if d['source_path'] else 'Saisie à la main'} for d in ech.listing(desk, mid)]
        try:
            prof = desk.db.execute("SELECT profile,state,updated FROM missions5614 WHERE matter=? AND profile<>'' ORDER BY updated DESC LIMIT 1", (mid,)).fetchone()
            out['profil'] = {'id': prof['profile'], 'mission_state': prof['state']} if prof else None
        except Exception:
            out['profil'] = None
    elif key == 'faits':
        facts = [_fact_view(desk, mid, r) for r in active]
        out['items'] = facts
        dated = [f for f in facts if f.get('event_date') and f['status'] in ('validated', 'pinned') and f.get('type') in ('event', 'completed_action', 'amount', 'claim', 'other')]
        dated.sort(key=lambda f: f['event_date'])
        out['rappel'] = dated
        out['refused'] = len(refused)
        out['counts'] = {'valides': sum(1 for f in facts if f['status'] in ('validated', 'pinned')), 'a_valider': sum(1 for f in facts if f['status'] == 'suggested'),
                         'refuses': len(refused), 'preuves_modifiees': sum(1 for f in facts if f.get('proof_state') == 'perimee')}
    elif key == 'chronologie':
        rows = fiche460.chronologie(desk, mid, 500)
        out['items'] = [r for r in rows if not r.get('undated')]
        out['undated'] = [r for r in rows if r.get('undated')]
    elif key == 'pretentions':
        out['items'] = [_fact_view(desk, mid, r) for r in active if r['record_type'] in ('claim', 'negotiation', 'instruction', 'open_question')]
        brief = _brief(desk, mid)
        data = (brief or {}).get('data') or {}
        out['synthese'] = {'claims': data.get('claims', []), 'negotiation_positions': data.get('negotiation_positions', []), 'open_questions': data.get('open_questions', []),
                           'version': (brief or {}).get('version')}
        matrix = []
        try:
            run = desk.db.execute('SELECT id,version,created FROM matrix_runs WHERE matter=? ORDER BY version DESC LIMIT 1', (mid,)).fetchone()
            if run:
                matrix = [dict(r) for r in desk.db.execute('SELECT proposition,asserted_by,proof_status,row_type,status,supporting_sources,contradicting_sources FROM evidence_matrix_rows WHERE run_id=? ORDER BY position LIMIT 200', (run['id'],))]
                out['matrice'] = {'version': run['version'], 'created': run['created'][:10], 'rows': matrix}
        except Exception:
            pass
        out['missions'] = _mission_items(desk, mid, ('matrice', 'analyse', 'conclusions', 'section'))
        try:
            sa = desk.db.execute('SELECT version,objective,status,data,created FROM strategy_analyses WHERE matter=? ORDER BY version DESC LIMIT 1', (mid,)).fetchone()
            out['strategie'] = {'version': sa['version'], 'objective': sa['objective'], 'status': sa['status'], 'created': sa['created'][:10], 'data': json.loads(sa['data'])} if sa else None
        except Exception:
            out['strategie'] = None
    elif key == 'pieces':
        out['items'] = _docs(desk, mid)
        out['counts'] = {'total': len(out['items']), 'erreurs': sum(1 for d in out['items'] if d['error'])}
    elif key == 'textes':
        try:
            from .legal_research import ensure_schema as lr_schema
            lr_schema(desk)
            out['items'] = [dict(r) for r in desk.db.execute('SELECT id,identifier,ecli,court,decision_date,title,official_url,exact_excerpt,verification_status,official_text_sha256 FROM legal_authorities_v240 WHERE matter=? ORDER BY updated DESC LIMIT 200', (mid,))]
            out['pistes'] = desk.db.execute("SELECT COUNT(*) FROM legal_research_leads_v240 WHERE matter=? AND status<>'verified'", (mid,)).fetchone()[0]
        except Exception:
            out['items'], out['pistes'] = [], 0
        out['missions'] = _mission_items(desk, mid, ('recherche',))
    elif key == 'ecritures':
        try:
            out['items'] = [dict(r) for r in desk.db.execute('SELECT id,request,kind,status,path,revision_of,updated FROM docreq520 WHERE matter=? ORDER BY updated DESC LIMIT 100', (mid,))]
        except Exception:
            out['items'] = []
        try:
            out['missions'] = [dict(r) for r in desk.db.execute('SELECT id,title,state,validation,validated_hash,updated FROM missions5614 WHERE matter=? ORDER BY updated DESC LIMIT 50', (mid,))]
        except Exception:
            out['missions'] = []
        out['drafts'] = [{'subject': r['subject'] or 'sans objet'} for r in desk.db.execute("SELECT subject FROM work_items WHERE matter=? AND state='draft_ready' ORDER BY updated DESC LIMIT 20", (mid,))]
    elif key == 'simulation':
        try:
            out['items'] = [dict(r) for r in desk.db.execute('SELECT version,objective,status,created FROM strategy_analyses WHERE matter=? ORDER BY version DESC LIMIT 20', (mid,))]
        except Exception:
            out['items'] = []
        try:
            out['actes'] = [dict(r) for r in desk.db.execute('SELECT version,act_type,title,status,created FROM act_projects WHERE matter=? ORDER BY version DESC LIMIT 20', (mid,))]
        except Exception:
            out['actes'] = []
        out['note'] = 'Une simulation motivée reste une hypothèse de travail : elle n’alimente jamais le registre des décisions obtenues.'
    elif key == 'decisions':
        words = ('jugement', 'ordonnance', 'arret', 'arrêt', 'decision', 'décision')
        out['items'] = [_fact_view(desk, mid, r) for r in active if r['record_type'] in ('event', 'completed_action') and any(w in r['title'].lower() for w in words)]
        try:
            out['avis'] = [dict(r) for r in desk.db.execute("SELECT path,status,result,updated FROM notices440 WHERE matter=? AND status='done' ORDER BY updated DESC LIMIT 20", (mid,))]
        except Exception:
            out['avis'] = []
    elif key == 'actions':
        f = fiche460.build_fiche(desk, mid)
        out['items'] = f['pending']
        out['next_deadline'] = f['next_deadline']
        out['tasks'] = [dict(t) for t in desk.db.execute("SELECT id,title,due,status FROM tasks WHERE matter=? AND status='open' ORDER BY due LIMIT 50", (mid,))]
    elif key == 'synthese':
        brief = _brief(desk, mid)
        out['brief'] = brief
        out['counts'] = {'faits_valides': sum(1 for r in active if r['status'] in ('validated', 'pinned')), 'faits_a_valider': sum(1 for r in active if r['status'] == 'suggested'),
                         'faits_refuses': len(refused), 'pieces': len(_docs(desk, mid))}
        try:
            out['counts']['evenements'] = desk.db.execute("SELECT COUNT(*) FROM timeline_events WHERE matter=? AND status NOT IN ('stale','disputed','archived')", (mid,)).fetchone()[0]
        except Exception:
            out['counts']['evenements'] = 0
        out['items'] = [brief] if brief else []
    if busy and not out['items']:
        out['state'] = 'en_cours'
    elif out['items'] or out.get('echeances') or out.get('actes') or out.get('rappel') or out.get('missions') or out.get('matrice'):
        out['state'] = 'present'
    else:
        out['state'] = 'vide'
    synth = next((s for s in st['steps'] if s['step'] == 'synthese'), None)
    if key in ('synthese', 'pretentions', 'parties') and synth and synth['fresh'] is False:
        out['freshness'] = 'perimee'
    elif synth and synth['state'] == 'ok':
        out['freshness'] = 'a_jour'
    else:
        out['freshness'] = 'inconnue'
    return out


# ------------------------------------------------------------------------------------------------ rendu
def _a(prefix, href, label, cls=''):
    return '<a%s href="%s">%s</a>' % ((' class="%s"' % cls) if cls else '', e(prefix + href, quote=True), e(label))


def _badge(status, label):
    cls = {'validated': 'ok', 'pinned': 'ok', 'configured': 'ok', 'suggested': 'warn', 'derived': 'warn', 'disputed': 'bad', 'perimee': 'warn'}.get(status, '')
    return '<span class="fc-badge %s">%s</span>' % (cls, e(label))


def _fact_li(prefix, x):
    src = ' · '.join((_a(prefix, s['href'], s['label']) if s.get('href') else e(s['label'])) for s in x.get('sources', [])[:2]) or 'Source non précisée'
    when = (' <span class="fc-muted">%s</span>' % e(str(x.get('event_date', ''))[:10])) if x.get('event_date') else ''
    return '<li><strong>%s</strong>%s %s<br><span class="fc-src">Source : %s</span></li>' % (e(x.get('value') or x.get('title', '')), when, _badge(x['status'], x['status_label']), src)


def tabs_html(prefix, mid, active):
    q = lambda key: '/fiche?' + urlencode({'matter': mid, 'onglet': key})
    return '<nav class="fc-tabs" aria-label="Rubriques du dossier">%s</nav>' % ''.join(
        '<a class="fc-tab%s" href="%s"%s>%s</a>' % (' on' if key == active else '', e(prefix + q(key), quote=True), ' aria-current="page"' if key == active else '', e(label))
        for key, label in RUBRIQUES)


def analysis_html(prefix, mid, st):
    steps = ''.join('<li class="fc-step fc-s-%s"><span class="fc-type">%s</span> %s%s%s</li>' % (
        e(s['state']), e(s['label']), e(s['state_label']), (' · ' + e(s['finished'][:16].replace('T', ' '))) if s.get('finished') else '',
        (' · <span class="fc-badge warn">périmée</span>' if s['fresh'] is False else '') + ((' · <span class="fc-badge bad">%s</span>' % e(s['error'][:120])) if s.get('error') else '')) for s in st['steps'])
    retry = ''
    a_reprendre = [s['step'] for s in st['steps'] if s['state'] in ('erreur', 'interrompue', 'partielle')]   # 5.6.109 : seulement ce qui n'a pas abouti
    if st['overall'] == 'partielle' and a_reprendre:
        retry = ' <button type="button" class="ax-btn ghost" id="fc-reprendre" data-steps="%s">Reprendre les étapes en erreur</button>' % e(','.join(a_reprendre), quote=True)
    return ('<section class="ax-card fc-analysis" id="fc-analysis" data-matter="%s" data-job="%s"><h2>Analyse du dossier</h2><p>État : <strong>%s</strong>%s. '
            '<button type="button" class="ax-btn" id="fc-analyse">Analyser le dossier</button>%s <span id="fc-analyse-result" class="fc-muted"></span></p>%s'
            '<details><summary>Étapes et compteurs</summary><ul class="fc-list">%s</ul><p class="fc-muted">Révision courante des sources et faits : %s. Une demande en file n’est pas une analyse terminée ; '
            'les résultats antérieurs restent affichés avec leur fraîcheur.</p></details></section>') % (
        e(mid, quote=True), e(str(st['job'] or ''), quote=True), e(st['overall_label']), (' (dernière réussite : %s)' % e(st['last_success'][:16].replace('T', ' '))) if st['last_success'] else '',
        retry, couverture_html(st), steps, e(st['revision']))


def couverture_html(st):
    """5.6.109 (C04) : ce qui a réellement été lu, d'après les états conservés du parcours (jamais un texte rédigé par l'IA)."""
    c = st.get('couverture') or {}
    out = ''
    if st.get('attente'):
        out += '<p class="fc-muted" role="status"><span class="fc-badge warn">en attente</span> %s.</p>' % e(st['attente']['libelle'][:1].upper() + st['attente']['libelle'][1:])
    if not c.get('definie'):
        return out
    p, m = c['pieces'], c['courriels']
    out += ('<p class="fc-muted">Couverture : %d pièce(s) présente(s), %d indexée(s), %d lue(s) par le repérage des faits, %d transmise(s) au modèle pour la synthèse ; '
            '%d courriel(s), dont %d indexé(s).%s</p>') % (
        p['total'], p['traite'], p['faits'], p['modele'], m['total'], m['traite'],
        '' if c.get('inventaire_complet') else ' <span class="fc-badge warn">inventaire du répertoire incomplet</span>')
    if c.get('motifs'):
        out += '<ul class="fc-list">%s</ul>' % ''.join('<li><span class="fc-badge %s">%s</span> <strong>%d</strong> <span>%s</span> : <span>%s</span></li>' % (
            'bad' if x['etat'] == 'en_erreur' else 'warn', e(ETATS_COUVERTURE.get(x['etat'], x['etat'])), x['nombre'],
            'pièce(s)' if x['genre'] == 'piece' else 'courriel(s)', e(x['libelle'] or x['motif'])) for x in c['motifs'][:12])
    return out


def _add_task5688(desk, prefix, mid):
    """5.6.88 : créer une tâche directement depuis le dossier."""
    from .audiences5688 import members
    from .taches5688 import add_form
    return add_form(prefix, members(desk), origine='dossier', ref=mid, matter=mid, heading='Ajouter une tâche à ce dossier')


def render(desk, prefix, mid, key, data):
    """HTML d'une rubrique (hors synthèse et faits, rendues par web460 avec les sections historiques)."""
    empty = lambda text: '<p class="fc-empty">%s</p>' % e(text)
    if key == 'structure':
        from . import assertions5689
        return assertions5689.rendu_html(desk, prefix, mid, data['projection'])
    if key == 'conseil':
        from . import consultation5689
        from .membres5650 import current_author, viewer_role
        return consultation5689.rubrique_html(desk, prefix, mid, current_author() or 'cabinet', viewer_role())
    head = ''
    if data['state'] == 'en_cours':
        head = '<p class="fc-muted">Analyse en cours : les données ci-dessous sont celles de la dernière analyse terminée.</p>'
    if data.get('freshness') == 'perimee':
        head += '<p class="fc-muted"><span class="fc-badge warn">périmée</span> Sources ou faits modifiés depuis la dernière synthèse : relancez l’analyse.</p>'
    body = head
    if key == 'parties':
        from . import dossier5644   # 5.6.44 : chaque partie se corrige ou se supprime ; une partie manquante s'ajoute
        body += '<ul class="fc-list">%s</ul>' % (''.join(_fact_li(prefix, x)[:-5] + dossier5644.editor_html(mid, 'parties', x) + '</li>' for x in data['items'])
                                                 or '<li class="fc-empty">Aucune partie connue : lancez l’analyse ou validez les faits proposés.</li>')
        body += dossier5644.add_html(mid, 'parties') + dossier5644.removed_html(desk, mid, 'parties')
        if data.get('refused'):
            body += '<p class="fc-muted">%d partie(s) refusée(s) par l’avocat : non réutilisées.</p>' % data['refused']
    elif key == 'procedure':
        from . import dossier5644
        body += '<ul class="fc-list">%s</ul>' % (''.join('<li><span class="fc-type">%s</span> ' % e(x['label']) + _fact_li(prefix, x)[4:-5] + dossier5644.editor_html(mid, x['kind'], x) + '</li>'
                                                         for x in data['items']) or '<li class="fc-empty">Juridiction et numéro de RG non renseignés : aucune pièce ne les mentionne encore.</li>')
        shown = {x['kind'] for x in data['items']}
        body += ''.join(dossier5644.add_html(mid, k) for k in ('jurisdiction', 'case_number') if k not in shown)
        body += dossier5644.removed_html(desk, mid, 'jurisdiction') + dossier5644.removed_html(desk, mid, 'case_number')
        if data.get('profil'):
            body += '<p>Profil procédural retenu par les missions : <strong>%s</strong> (mission %s).</p>' % (e(data['profil']['id']), e(data['profil']['mission_state']))
        body += '<h3>Actes de procédure</h3><ul class="fc-list">%s</ul>' % (''.join(_fact_li(prefix, x) for x in data.get('actes', [])) or '<li class="fc-empty">Aucun acte enregistré dans les faits validés ou proposés.</li>')
        body += '<h3>Échéances</h3><ul class="fc-list">%s</ul>' % (''.join('<li>%s – <strong>%s</strong> %s<br><span class="fc-src">Source : %s · %s</span></li>' % (
            e(d['due_label'] or d['due'] or 'sans date'), e(d['label']), _badge('validated' if d['validated'] else 'suggested', 'Confirmée' if d['validated'] else d['status_label']), e(d['source']), _a(prefix, d['href'], 'voir'))
            for d in data.get('echeances', [])) or '<li class="fc-empty">Aucune échéance de procédure.</li>')
    elif key == 'chronologie':
        lines = ''.join('<li class="fc-line fc-k-%s"><time datetime="%s">%s</time><div><span class="fc-type">%s</span> <strong>%s</strong>%s<p class="fc-muted">%s</p><span class="fc-src">Source : %s</span></div></li>' % (
            e(r['kind']), e(r['at'][:10]), e(r['at'][:10]), e(r['kind_label']), e(r['title']), '' if r['validated'] else ' <span class="fc-badge warn">%s</span>' % e(r['status_label']),
            e(r['detail'][:200]), _a(prefix, r['href'], r['source']) if r['href'] else e(r['source'])) for r in data['items'][:200])
        body += '<p>%s</p><ol class="fc-timeline">%s</ol>' % (_a(prefix, '/chronologie?' + urlencode({'matter': mid}), 'Chronologie complète et filtres'), lines or '<li class="fc-empty">Aucun événement daté.</li>')
        if data.get('undated'):
            body += '<h3>Dates inconnues ou partielles</h3><ul class="fc-list">%s</ul>' % ''.join('<li><span class="fc-type">%s</span> %s <span class="fc-muted">(%s)</span></li>' % (e(r['kind_label']), e(r['title']), e(r['at'] or 'date inconnue')) for r in data['undated'][:100])
    elif key == 'pretentions':
        body += '<h3>Prétentions, positions et instructions (faits du dossier)</h3><ul class="fc-list">%s</ul>' % (''.join(_fact_li(prefix, x) for x in data['items']) or '<li class="fc-empty">Aucune prétention enregistrée.</li>')
        s = data.get('synthese') or {}
        if s.get('version'):
            body += '<h3>Synthèse v%s</h3>' % e(str(s['version']))
            for label, keyname in (('Demandes', 'claims'), ('Positions de négociation', 'negotiation_positions'), ('Questions ouvertes', 'open_questions')):
                if s.get(keyname):
                    body += '<p><strong>%s</strong></p><ul class="fc-list">%s</ul>' % (e(label), ''.join('<li>%s</li>' % e(str(x)) for x in s[keyname][:40]))
        if data.get('matrice'):
            m = data['matrice']
            body += '<h3>Matrice de preuves v%s (%s) — arguments des parties</h3><table class="fc-table"><thead><tr><th>Proposition</th><th>Soutenue par</th><th>Preuve</th><th>État</th></tr></thead><tbody>%s</tbody></table>' % (
                e(str(m['version'])), e(m['created']), ''.join('<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>' % (e(r['proposition'][:300]), e(r['asserted_by']), e(r['proof_status']), e(r['status'])) for r in m['rows']))
        if data.get('strategie'):
            st = data['strategie']
            body += '<h3>Analyse stratégique v%s (%s · %s)</h3><p class="fc-muted">%s</p>' % (e(str(st['version'])), e(st['created']), e(st['status']), e(st['objective'][:300]))
            d = st.get('data') or {}
            for keyname in ('arguments', 'arguments_adverses', 'risks', 'options', 'recommendation', 'summary'):
                v = d.get(keyname)
                if isinstance(v, list) and v:
                    body += '<p><strong>%s</strong></p><ul class="fc-list">%s</ul>' % (e(keyname.replace('_', ' ')), ''.join('<li>%s</li>' % e(json.dumps(x, ensure_ascii=False) if not isinstance(x, str) else x)[:400] for x in v[:30]))
                elif isinstance(v, str) and v:
                    body += '<p><strong>%s</strong> %s</p>' % (e(keyname), e(v[:1500]))
        for mi in data.get('missions', []):
            body += '<h3>Mission « %s » · %s (%s) <span class="fc-badge warn">proposition de l’agent</span></h3><p class="fc-muted">%s</p><ul class="fc-list">%s</ul>' % (
                e(mi['mission']), e(mi['type']), e(mi['created']), e(mi['summary'][:300]), ''.join('<li>%s%s</li>' % (e(str(i.get('texte', ''))[:400]), (' <span class="fc-muted">(%s)</span>' % e(str(i.get('partie') or i.get('statut') or ''))) if (i.get('partie') or i.get('statut')) else '') for i in mi['items'][:40]))
        if not (data['items'] or s.get('version') or data.get('matrice') or data.get('strategie') or data.get('missions')):
            body += empty('Aucun argument analysé : lancez l’analyse du dossier, une mission (conclusions, assignation) ou « Analyser la stratégie ».')
    elif key == 'pieces':
        from .fiche460 import doc_href
        body += '<p class="fc-muted">%d pièce(s) indexée(s), %d en erreur de lecture.</p><ul class="fc-list">%s</ul>' % (
            data['counts']['total'], data['counts']['erreurs'], ''.join('<li>%s <span class="fc-muted">%s</span>%s</li>' % (
                _a(prefix, doc_href(d['path'], mid), d['name']), e(d['modified']), (' <span class="fc-badge bad">%s</span>' % e(d['error'][:80])) if d['error'] else '') for d in data['items']) or '<li class="fc-empty">Aucune pièce indexée : lancez l’analyse du dossier.</li>')
    elif key == 'textes':
        body += '<ul class="fc-list">%s</ul>' % (''.join('<li><strong>%s</strong> %s<br><span class="fc-muted">%s</span><br><a href="%s" rel="noopener noreferrer" target="_blank">texte officiel</a> · <span class="fc-src">empreinte %s</span></li>' % (
            e(x['identifier'] or x['ecli'] or x['title']), _badge('validated' if x['verification_status'] == 'verified' else 'suggested', 'vérifiée' if x['verification_status'] == 'verified' else x['verification_status']),
            e((x['exact_excerpt'] or '')[:300]), e(x['official_url'], quote=True), e((x['official_text_sha256'] or '')[:12])) for x in data['items']) or '<li class="fc-empty">Aucune référence vérifiée pour ce dossier.</li>')
        if data.get('pistes'):
            body += '<p class="fc-muted">%d piste(s) de recherche non vérifiée(s) : non citables.</p>' % data['pistes']
    elif key == 'ecritures':
        body += '<h3>Documents demandés au Pilote</h3><ul class="fc-list">%s</ul>' % (''.join('<li><strong>%s</strong> <span class="fc-muted">%s · %s · %s</span>%s</li>' % (
            e(x['request'][:160]), e(x['kind']), e(x['status']), e(str(x['updated'])[:10]), (' · ' + e(PurePosixPath(x['path']).name)) if x['path'] else '') for x in data['items']) or '<li class="fc-empty">Aucun document demandé.</li>')
        body += '<h3>Missions complexes</h3><ul class="fc-list">%s</ul>' % (''.join('<li>%s <span class="fc-muted">%s · validation : %s</span></li>' % (
            _a(prefix, '/missions-complexes?id=' + x['id'], x['title']), e(x['state']), e(x['validation'])) for x in data.get('missions', [])) or '<li class="fc-empty">Aucune mission.</li>')
        body += '<h3>Brouillons de courriels</h3><ul class="fc-list">%s</ul>' % (''.join('<li>%s</li>' % e(d['subject']) for d in data.get('drafts', [])) or '<li class="fc-empty">Aucun brouillon en attente.</li>')
    elif key == 'simulation':
        body += '<p class="fc-muted">%s</p>' % e(data['note'])
        body += '<ul class="fc-list">%s</ul>' % (''.join('<li>Analyse stratégique v%s · %s · %s<br><span class="fc-muted">%s</span></li>' % (e(str(x['version'])), e(x['status']), e(str(x['created'])[:10]), e(x['objective'][:300])) for x in data['items']) or '<li class="fc-empty">Aucune simulation.</li>')
        if data.get('actes'):
            body += '<h3>Projets d’actes</h3><ul class="fc-list">%s</ul>' % ''.join('<li>%s · %s · %s</li>' % (e(x['title']), e(x['act_type']), e(x['status'])) for x in data['actes'])
    elif key == 'decisions':
        body += '<ul class="fc-list">%s</ul>' % (''.join(_fact_li(prefix, x) for x in data['items']) or '<li class="fc-empty">Aucune décision (jugement, ordonnance, arrêt) enregistrée dans les faits.</li>')
        if data.get('avis'):
            body += '<h3>Avis et actes analysés</h3><ul class="fc-list">%s</ul>' % ''.join('<li>%s <span class="fc-muted">%s</span></li>' % (e(PurePosixPath(x['path']).name), e(str(x['updated'])[:10])) for x in data['avis'])
    elif key == 'actions':
        body += '<ul class="fc-list">%s</ul>' % (''.join('<li>%s</li>' % _a(prefix, p['href'], p['label']) for p in data['items']) or '<li class="fc-empty">Rien en attente.</li>')
        body += _add_task5688(desk, prefix, mid)
        if data.get('tasks'):
            body += '<h3>Tâches ouvertes</h3><ul class="fc-list">%s</ul>' % ''.join('<li>%s%s</li>' % (e(t['title']), (' <span class="fc-muted">(échéance %s)</span>' % e(t['due'][:10])) if t['due'] else '') for t in data['tasks'])
    return '<section class="ax-card fc-rubrique" id="rubrique-%s"><h2>%s</h2>%s</section>' % (e(key), e(data['label']), body)


def synthese_html(prefix, mid, data):
    brief = data.get('brief')
    c = data['counts']
    body = '<p class="fc-muted">%d fait(s) validé(s), %d à valider, %d refusé(s) · %d pièce(s) indexée(s) · %d événement(s) de chronologie.</p>' % (
        c['faits_valides'], c['faits_a_valider'], c['faits_refuses'], c['pieces'], c.get('evenements', 0))
    if data.get('freshness') == 'perimee':
        body += '<p><span class="fc-badge warn">périmée</span> Sources ou faits modifiés depuis la synthèse v%s : relancez l’analyse.</p>' % e(str((brief or {}).get('version', '')))
    if not brief:
        body += '<p class="fc-empty">Aucune synthèse calculée : lancez « Analyser le dossier » (la synthèse exige le modèle configuré).</p>'
    else:
        d = brief['data']
        body += '<p class="fc-muted">Synthèse v%s du %s (proposition du modèle, relue par l’avocat avant toute réutilisation).</p>' % (e(str(brief['version'])), e(str(brief['created'])[:10]))
        for label, keyname in (('Parties', 'parties'), ('Demandes', 'claims'), ('Dernières instructions', 'latest_instructions'), ('Positions de négociation', 'negotiation_positions'),
                               ('Actes accomplis', 'actions_completed'), ('Actions prévues', 'actions_planned'), ('Questions ouvertes', 'open_questions')):
            v = d.get(keyname)
            if isinstance(v, list) and v:
                body += '<p><strong>%s</strong></p><ul class="fc-list">%s</ul>' % (e(label), ''.join('<li>%s</li>' % e(str(x))[:400] for x in v[:30]))
        for label, keyname in (('Chronologie', 'chronology'), ('Échéances', 'deadlines'), ('Montants', 'amounts')):
            v = d.get(keyname)
            if isinstance(v, list) and v:
                body += '<p><strong>%s</strong></p><ul class="fc-list">%s</ul>' % (e(label), ''.join('<li>%s — %s</li>' % (e(str(x.get('date', ''))), e(str(x.get('text', ''))[:300])) for x in v[:40] if isinstance(x, dict)))
        if d.get('jurisdiction') or d.get('case_number'):
            body += '<p class="fc-muted">Juridiction : %s · RG : %s (selon la synthèse, à confirmer dans l’onglet Juridiction et procédure).</p>' % (e(str(d.get('jurisdiction') or '—')), e(str(d.get('case_number') or '—')))
    return '<section class="ax-card fc-rubrique" id="rubrique-synthese"><h2>Synthèse du dossier</h2>%s</section>' % body


def rappel_html(prefix, mid, data):
    items = data.get('rappel', [])
    c = data.get('counts', {})
    body = '<p class="fc-muted">Faits validés et datés, dans l’ordre chronologique ; chaque ligne renvoie à sa source. %d validé(s), %d à valider, %d refusé(s)%s.</p>' % (
        c.get('valides', 0), c.get('a_valider', 0), c.get('refuses', 0), (', %d dont la preuve a changé (à recontrôler)' % c['preuves_modifiees']) if c.get('preuves_modifiees') else '')
    body += '<ol class="fc-list">%s</ol>' % (''.join('<li><time>%s</time> — %s<br><span class="fc-src">Source : %s</span></li>' % (
        e(str(x['event_date'])[:10]), e(x.get('value') or x['title']), ' · '.join((_a(prefix, s['href'], s['label']) if s.get('href') else e(s['label'])) for s in x['sources'][:2]) or 'non précisée') for x in items)
        or '<li class="fc-empty">Aucun fait validé et daté : validez les faits proposés ci-dessous (date confirmée) pour constituer le rappel des faits.</li>')
    return '<section class="ax-card fc-rubrique" id="rubrique-rappel"><h2>Rappel des faits</h2>%s</section>' % body
