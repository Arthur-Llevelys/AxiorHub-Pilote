"""5.6.97 (lot fiabilite, B04) : « Préparer une réponse » de la vue Courriels, décision et commande de préparation dans une même transaction.

Avant la 5.6.97, la décision « en préparation » était validée, puis la préparation demandée par un second pas ; une coupure entre les deux laissait
une ligne « En préparation » sans aucun travail, pour toujours. Désormais :

- la commande est écrite dans la transaction de la décision (``inscrire``), avec la clé d'idempotence de la décision : une clé, une commande ;
- elle est exécutée juste après la validation, sous bail (``executer``) : un seul exécutant à la fois ; avant l'effet, la ligne est relue (même
  version, décision non annulée) et le travail déjà produit pour ce message est rattaché au lieu d'être redemandé ;
- une commande restée sans suite (service coupé) n'est jamais rejouée en silence : la ligne passe à « Interrompu », l'avocat la reprend d'un
  clic, ou la même demande rejouée (même clé) la reprend une fois ;
- les lignes historiques « En préparation » sans travail ni commande sont projetées « Interrompu » (aucune écriture, aucun renvoi).

Seule la preuve d'un travail de préparation (identifiant de ``jobs``) clôt la commande. Rien n'est envoyé ici.
"""
import time
import uuid

from .common import Stop

SCHEMA = '''
CREATE TABLE IF NOT EXISTS courriels5697_commandes(id TEXT PRIMARY KEY, cle TEXT NOT NULL UNIQUE, suivi_id TEXT NOT NULL, evenement TEXT NOT NULL,
    fil TEXT NOT NULL, mail_key TEXT NOT NULL, matter TEXT NOT NULL DEFAULT '', instruction TEXT NOT NULL DEFAULT '', auteur TEXT NOT NULL,
    version INTEGER NOT NULL, etat TEXT NOT NULL, bail REAL NOT NULL DEFAULT 0, tentatives INTEGER NOT NULL DEFAULT 0, job_id INTEGER,
    erreur TEXT NOT NULL DEFAULT '', cree REAL NOT NULL, depuis TEXT NOT NULL, maj TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS courriels5697_commandes_suivi ON courriels5697_commandes(suivi_id, etat);
'''
BAIL_SECONDES = 120        # une préparation demandée (lecture du message, mise en file) tient largement dans ce délai
RELANCE_SECONDES = 30      # commande jamais prise depuis ce délai : la requête qui l'a écrite s'est arrêtée
FINALES = ('fait', 'echec', 'sans_objet', 'remplacee')
INTERROMPU = 'Préparation interrompue avant son démarrage. Rien n’a été envoyé : reprenez-la.'
_horloge = time.time


def ensure_schema(desk):
    desk.db.executescript(SCHEMA)


def _maintenant_iso():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def inscrire(desk, suivi_id, evenement, cle, fil, courante, data, auteur, depuis):
    """Commande écrite dans la transaction de la décision (aucune validation ici). ``depuis`` : instant de la décision, borne de la relecture
    des travaux déjà produits. Une commande antérieure du même fil restée sans suite est remplacée (elle n'est plus rejouable)."""
    version = desk.db.execute('SELECT version FROM courriels5696_suivis WHERE id=?', (suivi_id,)).fetchone()[0]
    desk.db.execute("UPDATE courriels5697_commandes SET etat='remplacee', bail=0, maj=? WHERE suivi_id=? AND etat IN ('a_traiter','en_cours')", (depuis, suivi_id))
    ident = uuid.uuid4().hex
    desk.db.execute('INSERT INTO courriels5697_commandes(id, cle, suivi_id, evenement, fil, mail_key, matter, instruction, auteur, version, etat, cree, depuis, maj) '
                    "VALUES(?,?,?,?,?,?,?,?,?,?,'a_traiter',?,?,?)",
                    (ident, str(cle), suivi_id, evenement, fil, courante['mail_key'], str(data.get('dossier') or courante.get('matter') or ''),
                     str(data.get('instruction') or '')[:4000], auteur, version, _horloge(), depuis, depuis))
    return ident


def _depuis(desk, cmd):
    """Borne de relecture : la plus ancienne commande de ce fil restée sans travail (une coupure au milieu d'une commande remplacée compte)."""
    row = desk.db.execute("SELECT MIN(depuis) FROM courriels5697_commandes WHERE suivi_id=? AND job_id IS NULL AND etat NOT IN ('echec','sans_objet')",
                          (cmd['suivi_id'],)).fetchone()
    return min(x for x in (row[0], cmd['depuis']) if x)


def travail_produit(desk, mail_key, depuis):
    """Travail de préparation déjà demandé pour ce message depuis ``depuis`` (relecture avant tout rejeu), ou None."""
    if not depuis:
        return None
    row = desk.db.execute("SELECT id FROM jobs WHERE kind='prepare_reply' AND json_valid(args) AND json_extract(args,'$.key')=? AND created>=? ORDER BY id LIMIT 1",
                          (str(mail_key), str(depuis))).fetchone()
    return row[0] if row else None


def _clore(desk, ident, etat, job_id=None, erreur=''):
    desk.db.execute('UPDATE courriels5697_commandes SET etat=?, job_id=?, erreur=?, bail=0, maj=? WHERE id=?', (etat, job_id, str(erreur)[:120], _maintenant_iso(), ident))
    desk.db.commit()


def _rattacher(desk, cmd, job):
    """Le travail trouvé devient celui de la ligne (même écriture que la préparation normale), dans une transaction."""
    desk.db.execute('UPDATE courriels5696_suivis SET job_id=?, generation=generation+1 WHERE id=? AND job_id IS NULL', (job, cmd['suivi_id']))
    desk.db.execute('UPDATE courriels5696_evenements SET job_id=? WHERE id=? AND job_id IS NULL', (job, cmd['evenement']))
    desk.db.execute("UPDATE courriels5697_commandes SET etat='fait', job_id=?, bail=0, maj=? WHERE id=?", (job, _maintenant_iso(), cmd['id']))
    desk.db.commit()
    return job


def par_cle(desk, cle):
    ensure_schema(desk)
    row = desk.db.execute('SELECT * FROM courriels5697_commandes WHERE cle=?', (str(cle),)).fetchone()
    return dict(row) if row else None


def reprenable(cmd):
    """Commande que la même demande rejouée peut reprendre : jamais prise, ou prise et abandonnée (bail échu)."""
    return bool(cmd) and (cmd['etat'] == 'a_traiter' or (cmd['etat'] == 'en_cours' and cmd['bail'] < _horloge()))


def executer(desk, ident, effet):
    """Exécute la commande ``ident`` sous bail. ``effet()`` demande la préparation et rend l'identifiant du travail (ou lève). Rend l'identifiant
    du travail de la ligne, ou None si la commande n'a plus d'objet (autre décision entre-temps, déjà prise par un autre exécutant)."""
    t = _horloge()
    pris = desk.db.execute("UPDATE courriels5697_commandes SET etat='en_cours', bail=?, tentatives=tentatives+1, maj=? WHERE id=? AND "
                           "(etat='a_traiter' OR (etat='en_cours' AND bail<?))", (t + BAIL_SECONDES, _maintenant_iso(), ident, t)).rowcount
    desk.db.commit()
    if not pris:
        return None
    cmd = dict(desk.db.execute('SELECT * FROM courriels5697_commandes WHERE id=?', (ident,)).fetchone())
    suivi = desk.db.execute('SELECT statut, version FROM courriels5696_suivis WHERE id=?', (cmd['suivi_id'],)).fetchone()
    ev = desk.db.execute('SELECT job_id, annule FROM courriels5696_evenements WHERE id=?', (cmd['evenement'],)).fetchone()
    annulee = ev is None or bool(ev['annule'])
    if not annulee and ev['job_id']:                         # relecture : la préparation de CETTE décision a déjà été demandée (coupure après l'effet)
        _clore(desk, ident, 'fait', ev['job_id'])
        return ev['job_id']
    if not suivi or annulee or suivi['statut'] != 'en_preparation' or suivi['version'] != cmd['version']:
        _clore(desk, ident, 'sans_objet')                   # décision annulée ou suivie d'une autre : aucun effet
        return None
    deja = travail_produit(desk, cmd['mail_key'], _depuis(desk, cmd))
    if deja:
        return _rattacher(desk, cmd, deja)
    try:
        job = effet()
    except BaseException as error:
        if desk.db.in_transaction:
            desk.db.rollback()
        if desk.db.execute('SELECT 1 FROM courriels5696_evenements WHERE id=?', (cmd['evenement'],)).fetchone() is None:
            desk.db.execute('DELETE FROM courriels5697_commandes WHERE id=?', (ident,))   # décision défaite par l'échec : la commande avec elle
            desk.db.commit()
        elif isinstance(error, Exception):
            _clore(desk, ident, 'echec', None, str(error))
        raise                                             # interruption (arrêt du service) : bail conservé, reprise possible
    if job:
        _clore(desk, ident, 'fait', int(job))
    return job


def actives(desk):
    """Fils (``suivi_id``) dont la commande de préparation est en cours ou vient d'être écrite."""
    ensure_schema(desk)
    t = _horloge()
    return {r[0] for r in desk.db.execute("SELECT suivi_id FROM courriels5697_commandes WHERE (etat='a_traiter' AND cree>=?) OR (etat='en_cours' AND bail>=?)",
                                          (t - RELANCE_SECONDES, t))}


def annoter(desk, suivis):
    """Pose ``commande_active`` sur les suivis IMAP « en préparation » : faux = préparation interrompue (dernière commande ni aboutie ni en
    cours ; ligne historique sans commande ni travail). Lecture seule."""
    candidats = [s for s in suivis.values() if s.get('statut') == 'en_preparation' and not str(s.get('mail_key') or '').startswith('gmail:')]
    if not candidats:
        return suivis
    ensure_schema(desk)
    en_cours = actives(desk)
    for s in candidats:
        derniere = desk.db.execute("SELECT etat FROM courriels5697_commandes WHERE suivi_id=? AND etat<>'remplacee' ORDER BY cree DESC LIMIT 1", (s['id'],)).fetchone()
        if s['id'] in en_cours or (derniere and derniere['etat'] == 'fait'):
            continue
        if derniere or not s.get('job_id'):
            s['commande_active'] = False
    return suivis
