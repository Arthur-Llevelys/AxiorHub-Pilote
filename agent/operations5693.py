"""5.6.93 : une opération de livrable et son travail (audit 5.6.90, N01, N02, N08 et N10).

Les exports suivis (``livrables_export5689``) et les propositions IA (``propositions5689``) partagent trois contrats, ici, plutôt qu'une file parallèle :

- **Garde** : l'acteur noté lors de l'admission authentifiée (compte, rôle, cabinet) est résolu À NOUVEAU quand le travail agit : compte encore
  actif, rôle actuel (jamais plus élevé qu'à la demande), dossiers masqués et droits actuels, dossier déduit de l'objet et non des arguments du
  travail. Elle passe avant toute lecture, conversion, appel au modèle, publication et finalisation d'une reprise. Une opération ancienne sans
  acteur vérifiable demande une nouvelle autorisation : aucun repli vers un administrateur ni vers « cabinet ». Une révocation ferme l'opération
  avec son motif ; la politique des données et la pause restent des contrôles en plus, jamais à la place.
- **Création atomique** : schémas prêts avant ; dans un ``BEGIN IMMEDIATE`` court, l'opération, son travail (``enqueue(transactional=True)``) et
  ``job_id`` ; ``COMMIT`` puis notification. Un refus de file annule l'ensemble. La même clé retrouve la même paire ; d'autres paramètres avec la
  même clé sont refusés. Une ligne ancienne sans travail est réparée sous verrou, droits revérifiés, au plus un travail.
- **Projection** : un seul vocabulaire d'états entre ``jobs`` et les opérations (``STATUTS``, partagé avec l'outil Open WebUI). L'annulation et la
  fin du travail mettent à jour l'opération ; la lecture et la maintenance rattrapent un décalage après coupure. ``cancelled`` n'est jamais remis
  en ``pending`` ; une erreur reste une erreur jusqu'à une relance explicite, qui crée une NOUVELLE opération reliée à l'ancienne.

Aucun appel réseau ici : les annuaires et le registre des droits sont des fichiers et des bases locales.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import sqlite3

from .common import Stop

GENRES = {
    'export': {'table': 'prepared_exports5689', 'job': 'export_livrable5689', 'argument': 'export', 'demande': 'demande', 'actifs': ('demande', 'en_cours', 'ecriture'),
               'finalite': 'exporter'},
    'proposition': {'table': 'document_patches5689', 'job': 'modifier_livrable5689', 'argument': 'proposition', 'demande': 'demandee', 'actifs': ('demandee',),
                    'finalite': 'modifier_ia'},
}
PAR_TRAVAIL = {g['job']: k for k, g in GENRES.items()}
COLONNES = (('actor_id', "TEXT NOT NULL DEFAULT ''"), ('actor_role', "TEXT NOT NULL DEFAULT ''"), ('cabinet_id', "TEXT NOT NULL DEFAULT ''"),
            ('relance_de', "TEXT NOT NULL DEFAULT ''"))
RANG = {'assistant': 1, 'avocat': 2, 'administrateur': 3}
# Refus qui ferment l'opération : rien de plus ne sera lu ni écrit pour elle. Une panne du registre des droits n'en fait pas partie (échec à relancer).
REVOCATIONS = frozenset({'dossier_reserve', 'droit_export_retire', 'role_insuffisant', 'compte_desactive', 'autorisation_a_renouveler'})
ETATS_FERMES = {'acces_retire': 'Accès retiré : rien n’a été lu ni écrit de plus pour cette demande', 'a_autoriser': 'Autorisation à renouveler : demande ancienne sans auteur vérifiable',
                'annule': 'Annulé : aucune reprise automatique'}
MOTIFS = {'dossier_reserve': 'le dossier est désormais réservé à d’autres comptes', 'droit_export_retire': 'le droit d’exporter a été retiré à ce compte',
          'role_insuffisant': 'le rôle de ce compte ne permet plus cette action', 'compte_desactive': 'le compte qui a fait la demande est désactivé',
          'autorisation_a_renouveler': 'demande antérieure à la 5.6.93, sans auteur vérifiable : refaites-la', 'operation_annulee': 'annulée à votre demande',
          'controle_acces_indisponible': 'le registre des droits est illisible : rien n’a été fait'}

# Contrat de statut partagé (serveur, pages, outil Open WebUI) : état du travail -> (code, libellé, terminal).
STATUTS = {'pending': ('en_file', 'En file', False), 'running': ('en_cours', 'En cours', False), 'suspended': ('suspendu', 'Suspendu', False),
           'cancel_requested': ('arret_demande', 'Arrêt demandé ; confirmation encore attendue', False), 'cancelled': ('annule', 'Annulé ; aucune reprise automatique', True),
           'error': ('echec', 'Échec', True), 'done': ('termine', 'Terminé', True),
           # 5.6.94 (F02) : travail automatique passé car ses relances sont suspendues ; ni erreur ni réussite.
           'ignored': ('ignore', 'Ignoré : relances automatiques suspendues', True)}
ATTENTES = {'attente_budget': 'En attente : plafond de budget atteint', 'attente_fournisseur': 'En attente : fournisseur d’IA indisponible ou limite d’abonnement',
            'attente_decision': 'En attente d’une décision'}
SUSPENSIONS = {'pause': 'pause manuelle de l’IA', 'pause_dossier': 'dossier en pause', 'pause_horaire': 'pause programmée'}
LOT = 50


def _now():
    return datetime.now(timezone.utc).isoformat(timespec='microseconds')


# ------------------------------------------------------------------------------------------------ schéma
def ensure_schema(desk, genre):
    """Migration idempotente : colonnes d'acteur, de cabinet et de relance, index du travail associé. Aucune ligne ancienne n'est attribuée à quiconque."""
    table = GENRES[genre]['table']
    marque = '_ops5693_' + genre
    if getattr(desk, marque, None) is desk.db:
        return
    colonnes = {r[1] for r in desk.db.execute('PRAGMA table_info(%s)' % table)}
    for nom, definition in COLONNES:
        if nom not in colonnes:
            try:
                desk.db.execute('ALTER TABLE %s ADD COLUMN %s %s' % (table, nom, definition))
            except sqlite3.OperationalError as error:
                if 'duplicate column' not in str(error):     # deux processus migrent ensemble
                    raise
    desk.db.execute('CREATE INDEX IF NOT EXISTS %s_job ON %s(job_id)' % (table, table))
    desk.db.commit()
    setattr(desk, marque, desk.db)


def _tables(desk, genre):
    """Tables du service (créées si besoin, colonnes 5.6.93 comprises) : la maintenance peut passer avant le premier export ou la première proposition."""
    if genre == 'export':
        from .livrables_export5689 import schema
    else:
        from .propositions5689 import schema
    schema(desk)


def admission(owner, role):
    """Acteur noté à l'admission : il vient de la requête authentifiée, jamais du corps envoyé par le navigateur."""
    owner, role = str(owner or '')[:160], str(role or '')
    if not owner or role not in RANG:
        raise Stop('role_insuffisant')
    from .livrables5689 import CABINET
    return {'actor_id': owner, 'actor_role': role, 'cabinet_id': CABINET}


# ------------------------------------------------------------------------------------------------ garde
def acteur(desk, actor_id, role_admission):
    """Compte actif, rôle et droits ACTUELS de l'acteur. Le rôle retenu n'est jamais plus élevé qu'à la demande."""
    from . import membres5650
    actor_id, role = str(actor_id or ''), str(role_admission or '')
    if not actor_id or role not in RANG:
        raise Stop('autorisation_a_renouveler')
    annuaire = membres5650.directory(desk)
    vu = desk.db.execute('SELECT 1 FROM membres5650 WHERE owner=?', (actor_id,)).fetchone()
    if actor_id in annuaire:
        actuel = annuaire[actor_id].get('role')
        if actuel not in RANG:
            raise Stop('role_insuffisant')
        role = min(role, actuel, key=RANG.get)
    elif vu:            # membre connu, retiré de l'annuaire : son compte a été désactivé
        raise Stop('compte_desactive')
    state = desk.c['state_dir']
    return {'actor_id': actor_id, 'role': role, 'hidden': membres5650.hidden_for(state, actor_id, role), 'droits': membres5650.effective_rights(state, actor_id, role),
            'ai_denied': membres5650.ai_limits(state, actor_id, role)}


def garde(desk, genre, op, item, contexte=True):
    """Garde commune aux travaux de livrables, avant chaque effet. Rend l'acteur ; ``contexte`` le propage à la politique et au modèle."""
    from . import membres5650
    from .livrables5689 import CABINET
    if str(op['cabinet_id'] or '') != CABINET:
        raise Stop('autorisation_a_renouveler')
    if not item:
        raise Stop('livrable_absent')
    a = acteur(desk, op['actor_id'], op['actor_role'])
    if item['matter_id'] in a['hidden']:             # dossier déduit de l'objet lui-même, jamais des arguments du travail
        raise Stop('dossier_reserve')
    if item.get('required_right') and not a['droits'].get(item['required_right']):
        raise Stop('role_insuffisant')
    if GENRES[genre]['finalite'] == 'exporter' and not a['droits'].get('exporter'):
        raise Stop('droit_export_retire')
    if contexte:
        membres5650.set_author(a['actor_id'])
        membres5650.set_viewer(a['hidden'])
        membres5650.set_viewer_role(a['role'])
        membres5650.set_ai_denied(a['ai_denied'])
    return {**a, 'cabinet_id': CABINET, 'operation_id': op['id'], 'document_id': item['document_id'], 'version_id': item['version_id'], 'finalite': GENRES[genre]['finalite']}


@contextmanager
def isoler():
    """Le contexte posé par la garde (auteur, dossiers masqués, rôle, limites de l'IA) ne survit pas au travail : le suivant, sur le même fil, repart du sien."""
    from . import membres5650
    variables = (membres5650._viewer, membres5650._viewer_role, membres5650._author, membres5650._ai_denied)
    jetons = [v.set(v.get()) for v in variables]
    try:
        yield
    finally:
        for v, jeton in zip(variables, jetons):
            v.reset(jeton)


def fermer(desk, genre, ident, code):
    """Révocation constatée : l'opération active est fermée avec son motif. Une opération déjà terminée garde son état."""
    g = GENRES[genre]
    state = 'a_autoriser' if code == 'autorisation_a_renouveler' else 'acces_retire'
    changed = desk.db.execute('UPDATE %s SET state=?, error=?, updated=? WHERE id=? AND state IN (%s)' % (g['table'], ','.join('?' * len(g['actifs']))),
                              (state, str(code)[:120], _now(), ident, *g['actifs'])).rowcount
    desk.db.commit()
    if changed:
        desk.audit('operation5693_fermee', {'genre': genre, 'operation': str(ident)[:40], 'motif': str(code)[:80]})
    return changed


def proteger(desk, genre, op, item, contexte=True):
    """``garde`` qui ferme l'opération sur une révocation, puis lève le motif : le travail s'arrête sans aucun effet de plus."""
    try:
        return garde(desk, genre, op, item, contexte)
    except Stop as ex:
        code = str(ex).split(':')[0]
        if code in REVOCATIONS:
            fermer(desk, genre, op['id'], code)
        raise


# ------------------------------------------------------------------------------------------------ création atomique
def _ligne(desk, genre, **where):
    (k, v), = where.items()
    return desk.db.execute('SELECT * FROM %s WHERE %s=?' % (GENRES[genre]['table'], k), (v,)).fetchone()


@contextmanager
def _hors_travail(desk):
    """Réparation lancée par la maintenance : le travail recréé appartient à la demande de la personne, pas au travail de maintenance qui passe."""
    previous = getattr(desk, 'active_job_id', None)
    desk.active_job_id = None
    try:
        yield
    finally:
        desk.active_job_id = previous


def creer(desk, genre, colonnes, args_travail, origin=None):
    """Opération et travail dans UNE transaction. Rend (ligne, créée). Une clé déjà connue rend la ligne existante, sans rien créer."""
    g = GENRES[genre]
    cle = colonnes['idempotency_key']
    if desk.db.in_transaction:
        desk.db.commit()
    desk.db.execute('BEGIN IMMEDIATE')
    try:
        old = _ligne(desk, genre, idempotency_key=cle)        # relu sous le verrou : deux requêtes concurrentes retrouvent la même ligne
        if old:
            desk.db.rollback()
            return old, False
        desk.db.execute('INSERT INTO %s(%s) VALUES(%s)' % (g['table'], ','.join(colonnes), ','.join('?' * len(colonnes))), tuple(colonnes.values()))
        args = {g['argument']: colonnes['id'], **args_travail}
        job = desk.enqueue(g['job'], args, transactional=True, origin=origin)
        desk.db.execute('UPDATE %s SET job_id=? WHERE id=?' % g['table'], (job, colonnes['id']))
        desk.db.commit()
    except sqlite3.IntegrityError:
        if desk.db.in_transaction:
            desk.db.rollback()
        old = _ligne(desk, genre, idempotency_key=cle)
        if old:
            return old, False
        raise
    except BaseException:
        if desk.db.in_transaction:
            desk.db.rollback()
        raise
    desk.notify_queued(job, args)
    return _ligne(desk, genre, id=colonnes['id']), True


def _travail(desk, job_id):
    if not job_id:
        return None
    return desk.db.execute('SELECT * FROM jobs WHERE id=?', (int(job_id),)).fetchone()


def orpheline(desk, genre, row):
    """Opération en état de demande sans travail, ou dont le travail a disparu."""
    return row['state'] == GENRES[genre]['demande'] and (genre != 'proposition' or row['origin'] == 'ia') and _travail(desk, row['job_id']) is None


def reparer(desk, genre, ident, owner='', role=''):
    """Ancienne ligne sans travail : droits revérifiés, ligne verrouillée, au plus un travail. Une annulation ou une erreur ne sont jamais touchées.
    Une ligne sans acteur n'est reprise que par son propre demandeur authentifié (nouvelle autorisation) ; sinon elle attend cette autorisation."""
    from .livrables5689 import _item
    g = GENRES[genre]
    row = _ligne(desk, genre, id=ident)
    if not row or not orpheline(desk, genre, row):
        return row
    op = dict(row)
    if not op['actor_id']:
        if owner and role in RANG and owner == op['requested_by']:
            op.update(admission(owner, role))
        else:
            fermer(desk, genre, ident, 'autorisation_a_renouveler')
            return _ligne(desk, genre, id=ident)
    try:
        proteger(desk, genre, op, _item(desk, op['item_id']), contexte=False)
    except Stop as ex:
        if str(ex) == 'livrable_absent':      # plus rien à exporter ni à modifier : échec visible, la clé et l'historique restent
            desk.db.execute("UPDATE %s SET state='echec', error='livrable_absent', updated=? WHERE id=? AND state=?" % g['table'], (_now(), ident, g['demande']))
            desk.db.commit()
            return _ligne(desk, genre, id=ident)
        if str(ex).split(':')[0] in REVOCATIONS:
            return _ligne(desk, genre, id=ident)
        raise
    args = {g['argument']: ident, 'matter': (_item(desk, op['item_id']) or {}).get('matter_id', '')}
    if desk.db.in_transaction:
        desk.db.commit()
    with _hors_travail(desk):
        desk.db.execute('BEGIN IMMEDIATE')
        try:
            again = _ligne(desk, genre, id=ident)
            if not orpheline(desk, genre, again):           # un autre processus l'a réparée ou elle a changé d'état entre-temps
                desk.db.rollback()
                return again
            job = desk.enqueue(g['job'], args, transactional=True, origin='user')
            desk.db.execute('UPDATE %s SET job_id=?, actor_id=?, actor_role=?, cabinet_id=?, updated=? WHERE id=? AND state=?' % g['table'],
                            (job, op['actor_id'], op['actor_role'], op['cabinet_id'], _now(), ident, g['demande']))
            desk.db.commit()
        except BaseException:
            if desk.db.in_transaction:
                desk.db.rollback()
            raise
    desk.notify_queued(job, args)
    desk.audit('operation5693_reparee', {'genre': genre, 'operation': str(ident)[:40], 'job': job})
    return _ligne(desk, genre, id=ident)


# ------------------------------------------------------------------------------------------------ projection
def statut_travail(desk, job_id):
    """Contrat de statut partagé : état, libellé, terminal, motif d'attente, reprise annoncée si connue. ``sans_travail`` si le travail n'existe pas."""
    job = _travail(desk, job_id)
    if job is None:
        return {'job_id': job_id, 'statut': '', 'etat': 'sans_travail', 'libelle': 'Aucun travail associé', 'terminal': False, 'motif': '', 'reprise': None, 'message': ''}
    status = job['status']
    etat, libelle, terminal = STATUTS.get(status, ('inconnu', 'État inconnu', False))
    keys = job.keys()
    blocked = str(job['blocked_by'] or '') if 'blocked_by' in keys else ''
    reprise = job['not_before'] if 'not_before' in keys and job['not_before'] else None
    try:
        result = json.loads(job['result'] or '{}')
    except (TypeError, ValueError):
        result = {}
    result = result if isinstance(result, dict) else {}
    motif, message = '', str(result.get('message') or '')
    if status == 'pending' and blocked:
        from .budget_ledger5689 import ATTENTE_CODES, MESSAGES
        from .chatgpt5682 import HELD_CODES
        if blocked in ATTENTE_CODES:
            etat, motif, message = 'attente_budget', blocked, MESSAGES.get(blocked, 'Plafond atteint : rien n’a été transmis.')
        elif blocked in HELD_CODES:
            etat, motif = 'attente_fournisseur', blocked
        elif blocked == 'qualite_insuffisante':
            etat, motif, message = 'attente_decision', blocked, 'Aucun modèle qualifié pour ce travail : choisissez-en un dans Paramètres › IA.'
        libelle = ATTENTES.get(etat, libelle)
    elif status == 'suspended':
        motif = str(job['stop_reason'] or '')
        libelle = 'Suspendu : ' + SUSPENSIONS.get(motif, 'pause en vigueur')
        if motif == 'pause_horaire':
            try:
                from .horaires_ia5689 import etat_public
                public = etat_public(desk)
                reprise = public['prochaine_transition_utc'] if public['en_pause'] else reprise
                message = message or public['synthese']
            except (Stop, sqlite3.Error, KeyError, ValueError):
                pass
    elif status in ('error', 'cancelled'):
        motif = str(result.get('erreur') or job['stop_reason'] or '')
    return {'job_id': int(job['id']), 'statut': status, 'etat': etat, 'libelle': libelle, 'terminal': terminal, 'motif': motif, 'reprise': reprise, 'message': message[:300]}


def _terminer(desk, genre, row, job):
    """Transition finale idempotente : opération active dont le travail est terminé sans l'avoir conclue."""
    g = GENRES[genre]
    if row['state'] not in g['actifs'] or job is None or job['status'] not in ('cancelled', 'error'):
        return False
    if job['status'] == 'cancelled':
        state, code = 'annule', 'operation_annulee'
    else:
        try:
            result = json.loads(job['result'] or '{}')
        except (TypeError, ValueError):
            result = {}
        state, code = 'echec', str((result if isinstance(result, dict) else {}).get('erreur') or 'travail_en_echec')
    changed = desk.db.execute('UPDATE %s SET state=?, error=?, updated=? WHERE id=? AND state IN (%s)' % (g['table'], ','.join('?' * len(g['actifs']))),
                              (state, code[:120], _now(), row['id'], *g['actifs'])).rowcount
    return bool(changed)


def rapprocher(desk, genre, row):
    """Lecture : rattrape une fin de travail que l'opération n'a pas notée (coupure, ancienne version). Rend la ligne à jour."""
    if row is None or row['state'] not in GENRES[genre]['actifs'] or not row['job_id']:
        return row
    if _terminer(desk, genre, row, _travail(desk, row['job_id'])):
        desk.db.commit()
        return _ligne(desk, genre, id=row['id'])
    return row


def apres_arret(desk, job_id):
    """Appelé par ``Desk.cancel_job`` : l'opération d'un travail annulé avant démarrage est annulée aussitôt."""
    job = _travail(desk, job_id)
    genre = PAR_TRAVAIL.get(job['kind']) if job else None
    if not genre:
        return False
    _tables(desk, genre)
    row = desk.db.execute('SELECT * FROM %s WHERE job_id=?' % GENRES[genre]['table'], (int(job_id),)).fetchone()
    changed = bool(row) and _terminer(desk, genre, row, job)
    desk.db.commit()
    return changed


def apres_travail(desk, job_id, kind, status):
    """Appelé à la fin de ``Desk.work_once`` : même transition que ``rapprocher``, sans attendre une lecture."""
    if kind not in PAR_TRAVAIL or status not in ('cancelled', 'error'):
        return False
    return apres_arret(desk, job_id)


def vue(desk, genre, row, libelles):
    """Projection publique d'une opération : état métier, état réel du travail, terminal ou non, message et action possible."""
    g = GENRES[genre]
    state = row['state']
    suivi = statut_travail(desk, row['job_id']) if row['job_id'] else None
    active = state in g['actifs']
    if active and (suivi is None or suivi['etat'] == 'sans_travail') and (genre != 'proposition' or row['origin'] == 'ia'):
        suivi = {**(suivi or {}), 'etat': 'sans_travail', 'libelle': 'Aucun travail associé : renvoyez la même demande pour la reprendre', 'terminal': False}
    label = libelles.get(state) or ETATS_FERMES.get(state, state)
    if active and suivi and suivi['etat'] in ('suspendu', 'arret_demande', 'attente_budget', 'attente_fournisseur', 'attente_decision', 'en_file', 'sans_travail'):
        label = suivi['libelle']
    motif = MOTIFS.get(str(row['error'] or ''), '')
    action = ''
    if state in ('annule', 'echec', 'acces_retire', 'a_autoriser'):
        action = 'corriger_les_droits' if state == 'acces_retire' else 'relancer'
    return {'etat_affiche': label, 'suivi': suivi, 'terminal': not active, 'motif': motif, 'action': action, 'relance_de': row['relance_de'] if 'relance_de' in row.keys() else ''}


def relance(desk, genre, ident, document_id):
    """Relancer dans une NOUVELLE opération : l'ancienne doit exister, viser le même document et être terminée. Rend son identifiant à relier."""
    if not ident:
        return ''
    row = _ligne(desk, genre, id=str(ident))
    if not row or row['document_id'] != document_id:
        raise Stop('relance_invalide')
    row = rapprocher(desk, genre, row)
    if row['state'] in GENRES[genre]['actifs']:
        raise Stop('operation_encore_active')
    return row['id']


# ------------------------------------------------------------------------------------------------ maintenance et migration
def diagnostic(desk):
    """Diagnostic local, en lecture seule : opérations sans travail, opérations actives liées à un travail terminal, opérations sans acteur vérifiable."""
    out = {}
    for genre, g in GENRES.items():
        _tables(desk, genre)
        marks = ','.join('?' * len(g['actifs']))
        filtre = " AND o.origin='ia'" if genre == 'proposition' else ''
        sans = desk.db.execute("SELECT COUNT(*) FROM %s o LEFT JOIN jobs j ON j.id=o.job_id WHERE o.state=? AND j.id IS NULL%s" % (g['table'], filtre), (g['demande'],)).fetchone()[0]
        terminal = desk.db.execute("SELECT COUNT(*) FROM %s o JOIN jobs j ON j.id=o.job_id WHERE o.state IN (%s) AND j.status IN ('cancelled','error')" % (g['table'], marks), g['actifs']).fetchone()[0]
        anonymes = desk.db.execute("SELECT COUNT(*) FROM %s o WHERE o.state IN (%s) AND o.actor_id=''%s" % (g['table'], marks, filtre), g['actifs']).fetchone()[0]
        out[genre] = {'sans_travail': sans, 'actives_travail_termine': terminal, 'sans_acteur': anonymes}
    return out


def maintenance(desk, limite=LOT, pause=None):
    """Petits lots, curseur enregistré : projections des travaux terminés corrigées, puis orphelines en état de demande réparées (droits revérifiés).
    ``cancelled`` n'est jamais remis en file ; aucune autre ligne n'est modifiée. ``pause`` : point de contrôle avant chaque travail recréé."""
    rapport = {}
    for genre, g in GENRES.items():
        _tables(desk, genre)
        marks = ','.join('?' * len(g['actifs']))
        corrigees = 0
        for row in desk.db.execute("SELECT o.* FROM %s o JOIN jobs j ON j.id=o.job_id WHERE o.state IN (%s) AND j.status IN ('cancelled','error') LIMIT ?" % (g['table'], marks),
                                   (*g['actifs'], int(limite))).fetchall():
            corrigees += _terminer(desk, genre, row, _travail(desk, row['job_id']))
        desk.db.commit()
        cle = 'operations5693:curseur:' + genre
        curseur = str(desk.settings(cle, '') or '')
        filtre = " AND o.origin='ia'" if genre == 'proposition' else ''
        rows = desk.db.execute("SELECT o.id, o.created FROM %s o LEFT JOIN jobs j ON j.id=o.job_id WHERE o.state=? AND j.id IS NULL AND (o.created||o.id)>?%s ORDER BY o.created, o.id LIMIT ?"
                               % (g['table'], filtre), (g['demande'], curseur, int(limite))).fetchall()
        reparees, erreurs = 0, 0
        for r in rows:
            if pause:
                pause()
            try:
                after = reparer(desk, genre, r['id'])
                reparees += bool(after and after['job_id'] and _travail(desk, after['job_id']))
            except Stop:       # file pleine, pause : la ligne reste à reprendre au prochain passage
                erreurs += 1
            curseur = r['created'] + r['id']
        desk.setting(cle, curseur if len(rows) >= int(limite) else '')     # lot complet : on continue ; sinon le prochain passage repart du début
        rapport[genre] = {'projections_corrigees': corrigees, 'orphelines_reparees': reparees, 'reportees': erreurs}
    return rapport


def migrer(desk, appliquer=False):
    """Migration répétable 5.6.93 : diagnostic d'abord ; avec ``appliquer``, maintenance par lots jusqu'à épuisement (borné), puis nouveau diagnostic."""
    avant = diagnostic(desk)
    if not appliquer:
        return {'diagnostic': avant, 'applique': False}
    passes = []
    for _ in range(40):
        passes.append(maintenance(desk))
        if not any(v['orphelines_reparees'] or v['projections_corrigees'] for v in passes[-1].values()) and \
                not any(str(desk.settings('operations5693:curseur:' + g, '') or '') for g in GENRES):
            break
    return {'diagnostic': avant, 'applique': True, 'passes': passes, 'apres': diagnostic(desk)}
