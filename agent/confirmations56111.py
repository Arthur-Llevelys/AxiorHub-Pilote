"""5.6.111 (lot C, C03) : garde commune des confirmations par code à six chiffres.

Huit parcours demandent à l'avocat de recopier un code avant un effet réel (supervision, bordereau de pièces, projets de documents, projets
Word, dossiers d'audience, courriers sur modèle, programmes de travail, lots du pilote). Chacun garde son contrôle de droit, son aperçu et sa
réponse ; la garde ajoute ce qui manquait :

- un défi lié à l'acteur (personne de la requête quand elle est connue), à l'objet, à sa version (empreinte de l'aperçu), à l'empreinte de
  l'effet et à une expiration UTC ;
- chaque tentative, même fausse ou malformée, consomme un essai sous ``BEGIN IMMEDIATE`` ; au cinquième essai faux, la demande est bloquée ;
  une bonne valeur après expiration, blocage, remplacement ou modification de l'objet n'ouvre rien ;
- la consommation et l'intention durable (travail mis en file, état de l'objet) sont validées dans la même transaction ; aucun envoi (SMTP,
  HTTP, navigateur) n'a lieu pendant le verrou : les effets réseau viennent après, repris par la même clé (parcours, objet) et le même travail.

Reprise des demandes antérieures à la 5.6.111 : un défi encore enregistré dans l'ancienne forme (empreinte ``sha256(objet|code)`` de la table
du parcours) est adopté à sa première présentation : il reste confirmable jusqu'à son expiration prévue (au plus 24 h après l'adoption ; 30
minutes si l'expiration est illisible), puis suit la nouvelle règle (compteur, blocage, version).
"""
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import re
import secrets

from .common import Stop
from .erreurs5634 import Qualified

ESSAIS_MAX = 5
MARQUE = 'garde56111:'          # valeur posée dans la colonne d'empreinte des parcours : le code n'est plus vérifiable hors de la garde
ANCIEN_DEFAUT = timedelta(minutes=30)
ANCIEN_PLAFOND = timedelta(hours=24)
EN_COURS = ('running', 'cancel_requested')

SCHEMA = '''
CREATE TABLE IF NOT EXISTS confirmations56111(
  id TEXT PRIMARY KEY, chemin TEXT NOT NULL, objet TEXT NOT NULL, acteur TEXT NOT NULL DEFAULT '',
  version TEXT NOT NULL, effet TEXT NOT NULL, sel TEXT NOT NULL DEFAULT '', code_hash TEXT NOT NULL DEFAULT '',
  origine TEXT NOT NULL, cree TEXT NOT NULL, expire TEXT NOT NULL, essais INTEGER NOT NULL DEFAULT 0,
  etat TEXT NOT NULL, decide TEXT NOT NULL DEFAULT '', confirme_par TEXT NOT NULL DEFAULT '',
  executeur INTEGER, intention TEXT NOT NULL DEFAULT '');
CREATE UNIQUE INDEX IF NOT EXISTS confirmations56111_attente ON confirmations56111(chemin, objet) WHERE etat='attente';
CREATE INDEX IF NOT EXISTS confirmations56111_objet ON confirmations56111(chemin, objet, cree);
'''

# Messages lisibles (traduits en, de, es dans agent/locales/ecrans_*.json) ; ajoutés aux tables de messages sans remplacer un message existant.
MESSAGES = {
    'confirmation_bloquee': 'Demande bloquée après cinq codes faux : recommencez la préparation pour obtenir un nouveau code.',
    'confirmation_autre_utilisateur': 'Ce code a été demandé par une autre personne : préparez votre propre demande.',
    'confirmation_deja_en_cours': 'Cette confirmation est déjà en cours d’exécution : attendez la fin du travail en cours.',
    'objet_modifie_depuis_apercu': 'L’élément a changé depuis l’aperçu : préparez une nouvelle demande.',
    # codes historiques des parcours, sans message jusqu'ici (le détail « il reste N essais » s'y ajoute)
    'confirmation_supervisee_invalide': 'Code faux ou demande invalide.',
    'confirmation_document_invalide': 'Code faux ou demande invalide.',
    'confirmation_word_invalide': 'Code faux ou demande invalide.',
    'confirmation_audience_invalide': 'Code faux ou demande invalide.',
    'confirmation_programme_invalide': 'Code faux ou demande invalide.',
    'code_confirmation_incorrect': 'Code faux ou demande invalide.',
    'confirmation_lot_invalide': 'Code faux ou demande invalide.',
    'confirmation_supervisee_expiree': 'Le code a expiré : préparez une nouvelle demande.',
    'projet_document_expire': 'Le code a expiré : préparez une nouvelle demande.',
    'projet_word_expire': 'Le code a expiré : préparez une nouvelle demande.',
    'projet_audience_expire': 'Le code a expiré : préparez une nouvelle demande.',
    'programme_expire': 'Le code a expiré : préparez une nouvelle demande.',
    'confirmation_expiree': 'Le code a expiré : préparez une nouvelle demande.',
}
RESTE = 'Il reste %d essai(s) : recopiez les six chiffres affichés.'


def _maintenant():
    return datetime.now(timezone.utc)


def ensure_schema(desk):
    if not desk.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='confirmations56111'").fetchone():
        desk.db.executescript(SCHEMA)
        desk.db.commit()


def _empreinte(sel, code):
    return hashlib.sha256((str(sel) + '|' + str(code)).encode()).hexdigest()


def marque(garde):
    return MARQUE + str(garde)


def ancienne_empreinte(valeur):
    """Empreinte d'un défi de l'ancienne forme (64 caractères hexadécimaux) ; une marque de la garde ou une valeur vide n'en est pas une."""
    return bool(re.fullmatch(r'[a-f0-9]{64}', str(valeur or '')))


def _acteur():
    from .membres5650 import current_author
    return str(current_author() or '')


def _date(valeur):
    try:
        d = datetime.fromisoformat(str(valeur))
    except (TypeError, ValueError):
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def message(code):
    return MESSAGES.get(str(code or ''))


def expliquer(code, detail):
    """Message d'un code faux avec le nombre d'essais restants (``erreurs5634.explain``)."""
    base = MESSAGES.get(str(code or ''), 'Code faux ou demande invalide.')
    return base + ' ' + RESTE % int(detail.get('essais_restants56111', 0))


def emettre(desk, chemin, objet, version, effet, minutes, acteur=None):
    """Nouveau défi pour (parcours, objet) : remplace un défi encore en attente. Écritures laissées à la validation de l'appelant (même
    transaction que l'enregistrement de son aperçu). Rend {'code', 'expire', 'garde'} ; le code n'est jamais conservé en clair."""
    ensure_schema(desk)
    code = '%06d' % secrets.randbelow(1_000_000)
    garde, sel = secrets.token_hex(16), secrets.token_hex(16)
    maintenant = _maintenant()
    expire = (maintenant + timedelta(minutes=max(1, int(minutes)))).isoformat()
    desk.db.execute("UPDATE confirmations56111 SET etat='remplace', decide=? WHERE chemin=? AND objet=? AND etat='attente'",
                    (maintenant.isoformat(), str(chemin), str(objet)))
    desk.db.execute('INSERT INTO confirmations56111(id,chemin,objet,acteur,version,effet,sel,code_hash,origine,cree,expire,essais,etat) '
                    "VALUES(?,?,?,?,?,?,?,?,'5.6.111',?,?,0,'attente')",
                    (garde, str(chemin), str(objet), _acteur() if acteur is None else str(acteur), str(version), str(effet), sel,
                     _empreinte(sel, code), maintenant.isoformat(), expire))
    return {'code': code, 'expire': expire, 'garde': garde}


def annuler(desk, chemin, objet):
    """Refus ou abandon de l'objet par l'avocat : le défi en attente ne peut plus servir (écriture validée par l'appelant)."""
    ensure_schema(desk)
    desk.db.execute("UPDATE confirmations56111 SET etat='annule', decide=? WHERE chemin=? AND objet=? AND etat='attente'",
                    (_maintenant().isoformat(), str(chemin), str(objet)))


def etat(desk, chemin, objet):
    ensure_schema(desk)
    row = desk.db.execute('SELECT * FROM confirmations56111 WHERE chemin=? AND objet=? ORDER BY cree DESC, rowid DESC LIMIT 1',
                          (str(chemin), str(objet))).fetchone()
    return dict(row) if row else None


def _adopter(db, chemin, objet, version, effet, ancien, maintenant):
    expire = _date(ancien.get('expire'))
    if expire is None:
        expire = maintenant + ANCIEN_DEFAUT
    expire = min(expire, maintenant + ANCIEN_PLAFOND)
    garde = secrets.token_hex(16)
    db.execute('INSERT INTO confirmations56111(id,chemin,objet,acteur,version,effet,sel,code_hash,origine,cree,expire,essais,etat) '
               "VALUES(?,?,?,'',?,?,'','','ancienne',?,?,0,?)",
               (garde, chemin, objet, str(version), str(effet), maintenant.isoformat(), expire.isoformat(),
                'consomme' if ancien.get('etat') == 'consomme' else 'attente'))
    return db.execute('SELECT * FROM confirmations56111 WHERE id=?', (garde,)).fetchone()


def _code_juste(row, code, ancien):
    if not re.fullmatch(r'\d{6}', code):
        return False
    if row['origine'] == 'ancienne':
        try:
            return bool(ancien and ancien['verifier'](code))
        except Exception:   # noqa: BLE001 — empreinte ancienne illisible : code refusé
            return False
    return hmac.compare_digest(row['code_hash'], _empreinte(row['sel'], code))


def _travail_en_cours(db, job_id):
    row = db.execute('SELECT status FROM jobs WHERE id=?', (job_id,)).fetchone()
    return bool(row) and row[0] in EN_COURS


def confirmer(desk, chemin, objet, code, *, version, effet, code_faux, code_expire, code_deja, code_absent=None,
              code_malforme=None, code_modifie='objet_modifie_depuis_apercu', intention=None, ancien=None, reprise=False,
              effet_commence=False, matiere=''):
    """Présentation d'un code pour (parcours, objet). Rend {'resultat', 'reprise', 'garde'}.

    ``version`` et ``effet`` : empreintes recalculées par le parcours sur l'état ACTUEL de l'objet ; elles doivent égaler celles de
    l'aperçu. ``intention(db)`` : écritures durables du parcours (mise en file transactionnelle, état), exécutées dans la transaction de la
    consommation, sans validation ni appel réseau. ``ancien`` : {'verifier': fonction(code) -> bool, 'expire': iso, 'etat': 'attente' ou
    'consomme'} pour un défi de l'ancienne forme encore enregistré par le parcours. ``reprise`` : le parcours sait reprendre un effet déjà
    confirmé (création de fichiers) ; ``effet_commence`` : l'objet montre que l'effet a commencé (état « en création » ou « partiel »)."""
    from .membres5650 import viewer_hidden
    chemin, objet, code = str(chemin), str(objet), str(code or '').strip()
    if matiere and str(matiere) in viewer_hidden():
        raise Stop('dossier_reserve')   # droit retiré depuis l'aperçu : aucun essai décompté, rien n'est révélé
    ensure_schema(desk)
    db = desk.db
    if db.in_transaction:
        db.commit()   # écritures de lecture préalables du parcours (schéma, nettoyage) : validées avant de prendre le verrou
    acteur = _acteur()
    job = getattr(desk, 'active_job_id', None)
    db.execute('BEGIN IMMEDIATE')
    try:
        maintenant = _maintenant()   # relevée une fois le verrou obtenu : l'attente d'une autre écriture ne prolonge pas la validité
        row = db.execute('SELECT * FROM confirmations56111 WHERE chemin=? AND objet=? ORDER BY cree DESC, rowid DESC LIMIT 1',
                         (chemin, objet)).fetchone()
        if row is None and ancien:
            row = _adopter(db, chemin, objet, version, effet, ancien, maintenant)
        if row is None:
            raise Stop(code_absent or code_deja)
        if row['acteur'] and row['acteur'] != acteur and not (job is not None and not acteur):
            # aucun essai décompté : le code n'est pas évalué. Un travail de la file n'a pas de personne (la route qui l'a mis en file a
            # contrôlé ses droits) : il n'est pas refusé pour ce seul motif.
            raise Stop('confirmation_autre_utilisateur')
        if row['etat'] == 'consomme':
            if not reprise or not _code_juste(row, code, ancien):
                raise Stop(code_deja)
            if row['version'] != str(version) or row['effet'] != str(effet):
                raise Stop(code_modifie)
            executeur = row['executeur']
            if executeur is not None and executeur != job and _travail_en_cours(db, executeur):
                raise Stop('confirmation_deja_en_cours')
            if executeur is None and not effet_commence:
                raise Stop(code_deja)   # consommée par une confirmation directe qui n'a pas commencé l'effet : jamais deux exécutions
            db.execute('UPDATE confirmations56111 SET executeur=? WHERE id=?', (job, row['id']))
            db.commit()
            return {'resultat': None, 'reprise': True, 'garde': row['id']}
        if row['etat'] == 'bloque':
            raise Stop('confirmation_bloquee')
        if row['etat'] != 'attente':
            raise Stop(code_expire if row['etat'] == 'expire' else (code_modifie if row['etat'] == 'perime' else code_deja))
        expire = _date(row['expire'])
        if expire is None or maintenant >= expire:
            db.execute("UPDATE confirmations56111 SET etat='expire', decide=? WHERE id=?", (maintenant.isoformat(), row['id']))
            db.commit()
            raise Stop(code_expire)
        if row['version'] != str(version) or row['effet'] != str(effet):
            db.execute("UPDATE confirmations56111 SET etat='perime', decide=? WHERE id=?", (maintenant.isoformat(), row['id']))
            db.commit()
            raise Stop(code_modifie)
        essais = int(row['essais']) + 1
        if not _code_juste(row, code, ancien):
            bloque = essais >= ESSAIS_MAX
            db.execute('UPDATE confirmations56111 SET essais=?, etat=?, decide=? WHERE id=?',
                       (essais, 'bloque' if bloque else 'attente', maintenant.isoformat() if bloque else '', row['id']))
            db.commit()
            if bloque:
                raise Stop('confirmation_bloquee')
            restants = ESSAIS_MAX - essais
            raise Qualified((code_malforme or code_faux) if not re.fullmatch(r'\d{6}', code) else code_faux,
                            essais_restants56111=restants, precision=RESTE % restants)
        db.execute("UPDATE confirmations56111 SET essais=?, etat='consomme', decide=?, confirme_par=?, executeur=? WHERE id=?",
                   (essais, maintenant.isoformat(), acteur or ('travail:%s' % job if job is not None else ''), job, row['id']))
        resultat = intention(db) if intention else None
        if resultat is not None:
            db.execute('UPDATE confirmations56111 SET intention=? WHERE id=?', (str(resultat)[:200], row['id']))
        db.commit()
        return {'resultat': resultat, 'reprise': False, 'garde': row['id']}
    except BaseException:
        if db.in_transaction:
            db.rollback()
        raise


def decider(desk, operation):
    """Décision sans code (validation visuelle d'un modèle) : relecture et transition dans une seule transaction ``BEGIN IMMEDIATE``.
    ``operation(db)`` relit l'état et écrit ; elle lève ``Stop`` pour refuser. Aucun appel réseau pendant le verrou."""
    db = desk.db
    if db.in_transaction:
        db.commit()
    db.execute('BEGIN IMMEDIATE')
    try:
        resultat = operation(db)
        db.commit()
        return resultat
    except BaseException:
        if db.in_transaction:
            db.rollback()
        raise


def apres_mise_en_file(desk, job, kind, args):
    """Suites d'une mise en file transactionnelle, après validation (comme ``Desk.enqueue`` non transactionnel) : notification « demande
    enregistrée » et état du courriel pour un brouillon ; une panne ici ne défait pas la demande."""
    if not job:
        return
    desk.notify_queued(job, args)
    if kind in ('prepare_reply', 'prepare_draft', 'deposit_draft') and args and args.get('key'):
        try:
            from .integration import set_work_state
            set_work_state(desk, args['key'], 'drafting', 'demande_utilisateur')
        except Stop:
            pass
