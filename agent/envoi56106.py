"""5.6.106 (lot E) : options d'envoi de la messagerie — annulation pendant quelques secondes, envoi programmé, accusé de lecture,
importance haute.

Principe : la confirmation humaine existante (aperçu puis second clic, module d'envoi 4.9.0) ne fait plus partir le message ; elle le met dans une
**file d'envoi locale** (table ``envoi_file56106``) avec son heure de départ. Le message reste dans Brouillons tant qu'il n'est pas parti.
Le départ réel est fait par le **travailleur** (fil ``envoi56106`` du worker 1, ``start_send_lane``), jamais par le navigateur.

Garde-fous :
  - seule la confirmation humaine met un message dans la file (``mettre_en_file`` refuse un travail, un automatisme ou le jeton de
    service) ; un modèle ou une routine n'y a aucun accès ;
  - effet unique : réservation atomique (``en_attente`` → ``en_cours``, version comprise) avant tout effet ; l'étape (« réservé »,
    « SMTP commencé », « SMTP réussi ») est notée dans la même transaction que le journal d'envoi ; après une interruption, un message
    dont le SMTP n'a jamais commencé repart ; sinon on cherche son Message-ID dans Envoyés ; trouvé = parti, introuvable = « à vérifier »,
    signalé à la personne et JAMAIS renvoyé automatiquement ;
  - plafond horaire (20) compté au départ réel ; la pause générale (``pause5629.checkpoint`` et l'interrupteur « Pause ») bloque le
    départ et le signale ; le message part à la reprise, marqué « envoyé en retard » si l'heure prévue est dépassée ;
  - droits revus au départ (droit « envoyer » du compte, cloisonnement du brouillon) ; brouillon relu et identique à la confirmation.
"""
from datetime import datetime, timezone
import hashlib
import imaplib
import json
import logging
import re
import secrets
import sqlite3
import threading
import time

from .common import Stop

log = logging.getLogger(__name__)

PREFIXE = 'm56106/envoi/'
DELAIS = (0, 5, 10, 20, 30)
DELAI_DEFAUT = 10
CLE_DELAI = 'envoi56106:delai:'
RETARD = 120                     # secondes au-delà de l'heure prévue : « envoyé en retard »
ATTENTE = 30                     # nouvel essai après un empêchement passager (pause, plafond, messagerie injoignable)
AVANCE_MIN = 60                  # un envoi programmé est au moins une minute dans le futur
HORIZON = 366 * 86400            # et au plus un an
POLL = 1.0
PAR_PASSE = 5
CONSERVATION = 30 * 86400
ROUTES_GET = ('liste', 'etat')
ROUTES_POST = ('confirmer', 'annuler', 'maintenant', 'modifier', 'retirer', 'reglage')
ROLES = ('administrateur', 'avocat')
PASSAGERS = frozenset({'plafond_horaire_envois', 'automatismes_en_pause', 'ia_en_pause', 'controle_pause_indisponible',
                       'dossier_imap_inaccessible', 'uidvalidity_absent', 'connexion_imap_echouee', 'lecture_imap_echouee',
                       'imap_delai_depasse', 'imap_serveur_introuvable'})
DOUTES = frozenset({'envoi_incertain_verifiez_les_envoyes'})
BROUILLON_CHANGE = frozenset({'message_imap_disparu', 'uidvalidity_modifiee', 'message_non_brouillon', 'brouillon_modifie_depuis_apercu'})

SCHEMA = '''
CREATE TABLE IF NOT EXISTS envoi_file56106(
  id TEXT PRIMARY KEY, owner TEXT NOT NULL, role TEXT NOT NULL DEFAULT '', uid TEXT NOT NULL, validity TEXT NOT NULL,
  fingerprint TEXT NOT NULL, matter TEXT NOT NULL DEFAULT '', genre TEXT NOT NULL, prevu REAL NOT NULL, depart REAL NOT NULL,
  prochain REAL NOT NULL DEFAULT 0, options TEXT NOT NULL DEFAULT '{}', apercu TEXT NOT NULL DEFAULT '{}', etat TEXT NOT NULL,
  etape TEXT NOT NULL DEFAULT '', motif TEXT NOT NULL DEFAULT '', message_id TEXT NOT NULL DEFAULT '', bail TEXT NOT NULL DEFAULT '',
  log_id INTEGER, version INTEGER NOT NULL DEFAULT 1, retard INTEGER NOT NULL DEFAULT 0, masque INTEGER NOT NULL DEFAULT 0,
  cree REAL NOT NULL, parti REAL, maj REAL NOT NULL);
CREATE INDEX IF NOT EXISTS envoi_file56106_depart ON envoi_file56106(etat, depart);
CREATE INDEX IF NOT EXISTS envoi_file56106_owner ON envoi_file56106(owner, etat);
CREATE TABLE IF NOT EXISTS envoi_recus56106(
  cle TEXT NOT NULL, owner TEXT NOT NULL, route TEXT NOT NULL, objet TEXT NOT NULL, reponse TEXT NOT NULL, cree REAL NOT NULL,
  PRIMARY KEY(owner, cle));
'''

ETATS = {'en_attente': 'En attente de départ', 'en_cours': 'Départ en cours', 'parti': 'Envoyé', 'annule': 'Annulé',
         'echec': 'Non envoyé', 'a_verifier': 'À vérifier'}
MESSAGES = {
    'introuvable': 'Envoi introuvable.',
    'role_insuffisant': 'Votre rôle ne permet pas d’envoyer un message depuis AxiorHub.',
    'version_en_conflit': 'Cet envoi a changé entre-temps : la liste a été rechargée, recommencez.',
    'deja_parti': 'Trop tard : le message est déjà parti (ou part en ce moment).',
    'cle_deja_utilisee': 'Cette demande a déjà été utilisée pour une autre action : rechargez la page.',
    'date_envoi_invalide': 'Date ou heure d’envoi illisible : choisissez une date et une heure.',
    'heure_d_envoi_passee': 'Cette heure est passée (ou dans moins d’une minute) : choisissez une heure à venir.',
    'date_envoi_trop_lointaine': 'L’envoi programmé ne peut pas dépasser un an.',
    'delai_invalide': 'Délai d’annulation possible : 0, 5, 10, 20 ou 30 secondes.',
    'deja_en_file': 'Ce message attend déjà son départ (voir « Envois programmés »).',
    'envoi_reserve_a_une_personne': 'Seule une personne, par sa confirmation, peut mettre un message dans la file d’envoi.',
    'action_impossible': 'Action impossible dans l’état actuel de cet envoi.',
    'automatismes_en_pause': 'Départ bloqué : la pause générale est active. Le message partira à la reprise (il sera marqué « envoyé en retard »).',
    'ia_en_pause': 'Départ bloqué : la pause générale est active. Le message partira à la reprise.',
    'plafond_horaire_envois': 'Plafond de 20 envois par heure atteint : le message partira dès que possible.',
    'brouillon_modifie_depuis_confirmation': 'Le brouillon a été modifié ou retiré après la confirmation : rien n’est parti. Renvoyez-le depuis Brouillons.',
    'envoi_incertain_verifiez_les_envoyes': 'Un envoi de ce message a été interrompu : vérifiez vos éléments envoyés avant de le renvoyer.',
    'envoi_interrompu_a_verifier': 'Le départ a été interrompu en plein envoi et le message est introuvable dans Envoyés : vérifiez (Envoyés, '
                                   'destinataire) avant de le renvoyer. Rien n’est renvoyé automatiquement.',
    'copie_envoyes_non_verifiee': 'Envoyé, mais la copie dans les éléments envoyés n’a pas été retrouvée : ajoutez-la à la main.',
    'copie_envoyes_non_enregistree': 'Envoyé, mais la copie n’a pas pu être enregistrée dans les éléments envoyés : ajoutez-la à la main.',
    'droit_envoi_retire': 'Le droit d’envoyer de ce compte a été retiré depuis la confirmation : rien n’est parti.',
    'deja_envoye': 'Ce même message est déjà parti : il n’est pas renvoyé.',
    'reprise_apres_interruption': 'Départ repris après une interruption (le message n’était pas encore parti).',
    'controle_indisponible': 'Contrôle momentanément indisponible : réessayez.',
}
STATUTS = {'introuvable': '404 Not Found', 'role_insuffisant': '403 Forbidden', 'version_en_conflit': '409 Conflict', 'deja_parti': '409 Conflict',
           'cle_deja_utilisee': '409 Conflict', 'deja_en_file': '409 Conflict', 'controle_indisponible': '503 Service Unavailable'}


def ensure_schema(desk):
    """Au démarrage (``Desk._prepare``), jamais pendant une lecture."""
    desk.db.executescript(SCHEMA)
    desk.db.commit()


def message(code):
    if code in MESSAGES:
        return MESSAGES[code]
    try:
        from .web440 import human
        return human(code)
    except Exception:   # noqa: BLE001
        return code


# ------------------------------------------------------------------------------------------------ heures du cabinet
def fuseau(desk):
    from .acces5683 import today_of
    return today_of(desk)[1]


def _zone(desk):
    from zoneinfo import ZoneInfo
    try:
        return ZoneInfo(fuseau(desk))
    except Exception:   # noqa: BLE001 — fuseau illisible : UTC, annoncé par l'heure affichée
        return timezone.utc


JOURS = ('lundi', 'mardi', 'mercredi', 'jeudi', 'vendredi', 'samedi', 'dimanche')
MOIS = ('janvier', 'février', 'mars', 'avril', 'mai', 'juin', 'juillet', 'août', 'septembre', 'octobre', 'novembre', 'décembre')


def libelle(desk, ts):
    if not ts:
        return ''
    d = datetime.fromtimestamp(float(ts), _zone(desk))
    return '%s %d %s %d à %02d h %02d' % (JOURS[d.weekday()], d.day, MOIS[d.month - 1], d.year, d.hour, d.minute)


def programme(desk, texte, now):
    """« AAAA-MM-JJTHH:MM » lu dans le fuseau du cabinet → horodatage ; une heure passée (ou à moins d'une minute) est refusée."""
    m = re.fullmatch(r'(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})', str(texte or '').strip())
    if not m:
        raise Stop('date_envoi_invalide')
    try:
        ts = datetime(*(int(x) for x in m.groups()), tzinfo=_zone(desk)).timestamp()
    except (ValueError, OverflowError):
        raise Stop('date_envoi_invalide') from None
    if ts < now + AVANCE_MIN:
        raise Stop('heure_d_envoi_passee')
    if ts > now + HORIZON:
        raise Stop('date_envoi_trop_lointaine')
    return ts


# ------------------------------------------------------------------------------------------------ réglage
def delai(desk, owner):
    value = desk.settings(CLE_DELAI + str(owner or 'cabinet'), DELAI_DEFAUT)
    return value if value in DELAIS else DELAI_DEFAUT


def regler_delai(desk, owner, value, recu=None):
    try:
        value = int(value)
    except (TypeError, ValueError):
        raise Stop('delai_invalide') from None
    if value not in DELAIS:
        raise Stop('delai_invalide')
    desk.db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)', (CLE_DELAI + str(owner or 'cabinet'), json.dumps(value)))
    out = {'delai': value, 'message': 'Annulation possible pendant %d s après la confirmation.' % value if value else
           'Envoi sans délai d’annulation après la confirmation.'}
    _recu(desk, recu, out)
    desk.audit('envoi56106_delai', {'delai': value})
    return out


# ------------------------------------------------------------------------------------------------ mise en file
def _options(accuse=False, importance=False):
    return {'accuse': accuse is True, 'importance': importance is True}


def en_tetes(options, sender):
    """En-têtes ajoutés au départ : RFC 8098 (accusé de lecture, à l'adresse de l'expéditeur) et importance haute."""
    out = []
    if options.get('importance'):
        out += [('Importance', 'high'), ('X-Priority', '1')]
    if options.get('accuse') and sender:
        out.append(('Disposition-Notification-To', '<%s>' % sender))
    return out


def _humain_requis(desk):
    """Seule une personne met un message dans la file : jamais un travail de l'agent, un automatisme ni le jeton de service."""
    from .membres5650 import current_author
    if getattr(desk, 'active_job_id', None) or getattr(desk, 'origin_context', None) == 'automatic' or current_author() == 'jeton_service':
        raise Stop('envoi_reserve_a_une_personne')


def mettre_en_file(desk, owner, role, token, quand='', accuse=False, importance=False, now=None, recu=None):
    """Second clic de la personne : jeton consommé, brouillon contrôlé, message mis dans la file avec son heure de départ. N'envoie rien."""
    from . import web490   # seule la couche web d'envoi touche au module d'envoi
    _humain_requis(desk)
    now = time.time() if now is None else now
    owner = str(owner or 'cabinet')
    depart = programme(desk, quand, now) if str(quand or '').strip() else None   # heure refusée avant de consommer le jeton
    fp = web490.file_jeton(desk, token)
    if fp and desk.db.execute("SELECT 1 FROM envoi_file56106 WHERE fingerprint=? AND etat IN ('en_attente','en_cours')", (fp,)).fetchone():
        raise Stop('deja_en_file')
    info = web490.file_confirmer(desk, token, now)
    attente = delai(desk, owner)
    genre = 'programme' if depart is not None else 'differe'
    depart = depart if depart is not None else now + attente
    options = _options(accuse, importance)
    options['de'] = info['sender']   # adresse de l'expéditeur relue dans le brouillon confirmé (accusé de lecture)
    if options['accuse'] and not info['sender']:
        options['accuse'] = False
    s = info['summary']
    apercu = {'a': str(s.get('to') or '')[:300], 'cc': str(s.get('cc') or '')[:300], 'objet': str(s.get('subject') or '')[:200],
              'n': int(s.get('recipient_count') or 0), 'pj': len(s.get('attachments') or [])}
    ident = secrets.token_hex(12)
    desk.db.execute('INSERT INTO envoi_file56106(id,owner,role,uid,validity,fingerprint,matter,genre,prevu,depart,options,apercu,etat,cree,maj) '
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,'en_attente',?,?)",
                    (ident, owner, str(role or ''), info['uid'], info['validity'], info['fingerprint'], info['matter'], genre, depart, depart,
                     json.dumps(options), json.dumps(apercu, ensure_ascii=False), now, now))
    out = {'file': True, 'id': ident, 'version': 1, 'genre': genre, 'delai': attente if genre == 'differe' else None,
           'depart': datetime.fromtimestamp(depart, _zone(desk)).isoformat(timespec='seconds'), 'depart_libelle': libelle(desk, depart),
           'reste': max(0, int(round(depart - now))), 'uid': info['uid'], 'uidvalidity': info['validity'],
           'options': {'accuse': options['accuse'], 'importance': options['importance']},
           'message': ('Envoi programmé le %s : le message reste dans Brouillons jusqu’à son départ.' % libelle(desk, depart)) if genre == 'programme'
           else ('Envoi dans %d s.' % attente if attente else 'Envoi en cours…')}
    _recu(desk, recu, out)
    desk.audit('envoi56106_file', {'genre': genre, 'delai': attente, 'options': sorted(k for k in ('accuse', 'importance') if options[k])})
    return out


# ------------------------------------------------------------------------------------------------ actions sur un envoi
def _ligne(desk, owner, ident):
    """Envoi de cette personne ; absent ou d'un autre compte : même 404 neutre."""
    row = desk.db.execute('SELECT * FROM envoi_file56106 WHERE id=? AND owner=?', (str(ident or '')[:40], str(owner or 'cabinet'))).fetchone()
    if not row:
        raise Stop('introuvable')
    return row


def _version(row, version):
    if version not in (None, '') and str(version) != str(row['version']):
        raise Stop('version_en_conflit')


def _refus(desk, owner, ident, attendus):
    row = _ligne(desk, owner, ident)
    if row['etat'] in ('en_cours', 'parti'):
        raise Stop('deja_parti')
    if row['etat'] in attendus:
        raise Stop('version_en_conflit')
    raise Stop('action_impossible')


def annuler(desk, owner, ident, version=None, now=None, recu=None):
    """« Annuler » : le message ne part pas ; il est toujours dans Brouillons (il n'en a jamais été retiré)."""
    now = time.time() if now is None else now
    row = _ligne(desk, owner, ident)
    if row['etat'] == 'annule':
        out = {'annule': True, 'uid': row['uid'], 'uidvalidity': row['validity'], 'message': 'Envoi déjà annulé : le message est dans Brouillons.'}
        _recu(desk, recu, out)
        return out
    if row['etat'] in ('en_cours', 'parti'):
        raise Stop('deja_parti')
    _version(row, version)
    n = desk.db.execute("UPDATE envoi_file56106 SET etat='annule', motif='', apercu='{}', version=version+1, maj=? "
                        "WHERE id=? AND owner=? AND etat='en_attente' AND version=?", (now, row['id'], row['owner'], row['version'])).rowcount
    if n != 1:
        desk.db.rollback()
        _refus(desk, owner, ident, ('en_attente',))
    out = {'annule': True, 'uid': row['uid'], 'uidvalidity': row['validity'],
           'message': 'Envoi annulé : rien n’est parti, le message est resté dans Brouillons.'}
    _recu(desk, recu, out)
    desk.audit('envoi56106_annule', {'genre': row['genre']})
    return out


def maintenant(desk, owner, ident, version=None, now=None, recu=None):
    """« Envoyer maintenant » (ou « Réessayer » après un échec franc) : départ avancé ; c'est toujours le travailleur qui envoie."""
    now = time.time() if now is None else now
    row = _ligne(desk, owner, ident)
    if row['etat'] in ('en_cours', 'parti'):
        raise Stop('deja_parti')
    if row['etat'] not in ('en_attente', 'echec'):
        raise Stop('action_impossible')
    _version(row, version)
    n = desk.db.execute("UPDATE envoi_file56106 SET etat='en_attente', depart=?, prochain=0, motif='', version=version+1, maj=? "
                        "WHERE id=? AND owner=? AND etat IN ('en_attente','echec') AND version=?", (now, now, row['id'], row['owner'], row['version'])).rowcount
    if n != 1:
        desk.db.rollback()
        _refus(desk, owner, ident, ('en_attente', 'echec'))
    out = {'id': row['id'], 'version': row['version'] + 1, 'message': 'Départ demandé : le message part dans un instant.'}
    _recu(desk, recu, out)
    desk.audit('envoi56106_maintenant', {'genre': row['genre'], 'reprise': row['etat'] == 'echec'})
    return out


def modifier(desk, owner, ident, quand, accuse=False, importance=False, version=None, now=None, recu=None):
    """« Modifier » : nouvelle date et heure de départ, options. Pour changer le texte : annuler, puis reprendre le brouillon."""
    now = time.time() if now is None else now
    row = _ligne(desk, owner, ident)
    if row['etat'] in ('en_cours', 'parti'):
        raise Stop('deja_parti')
    if row['etat'] != 'en_attente':
        raise Stop('action_impossible')
    _version(row, version)
    depart = programme(desk, quand, now)
    options = {**_options(accuse, importance), 'de': json.loads(row['options'] or '{}').get('de', '')}
    options['accuse'] = options['accuse'] and bool(options['de'])
    n = desk.db.execute("UPDATE envoi_file56106 SET genre='programme', prevu=?, depart=?, prochain=0, motif='', options=?, version=version+1, maj=? "
                        "WHERE id=? AND owner=? AND etat='en_attente' AND version=?",
                        (depart, depart, json.dumps(options), now, row['id'], row['owner'], row['version'])).rowcount
    if n != 1:
        desk.db.rollback()
        _refus(desk, owner, ident, ('en_attente',))
    out = {'id': row['id'], 'version': row['version'] + 1, 'depart_libelle': libelle(desk, depart),
           'message': 'Envoi programmé le %s.' % libelle(desk, depart)}
    _recu(desk, recu, out)
    desk.audit('envoi56106_modifie', {'options': sorted(k for k in ('accuse', 'importance') if options[k])})
    return out


def retirer(desk, owner, ident, now=None, recu=None):
    """Retire de la liste un envoi terminé ou à vérifier (aucun effet sur la messagerie)."""
    now = time.time() if now is None else now
    row = _ligne(desk, owner, ident)
    if row['etat'] not in ('echec', 'a_verifier', 'parti', 'annule'):
        raise Stop('action_impossible')
    desk.db.execute("UPDATE envoi_file56106 SET masque=1, apercu='{}', version=version+1, maj=? WHERE id=?", (now, row['id']))
    out = {'retire': True, 'message': 'Retiré de la liste.'}
    _recu(desk, recu, out)
    return out


def _recu(desk, recu, out):
    """Clé d'idempotence : le reçu est écrit dans la même transaction que l'effet, puis tout est validé."""
    if recu and recu[0]:
        cle, owner, route, objet = recu
        desk.db.execute('INSERT OR REPLACE INTO envoi_recus56106 VALUES(?,?,?,?,?,?)',
                        (cle, owner, route, objet, json.dumps(out, ensure_ascii=False, default=str), time.time()))
    desk.db.commit()


# ------------------------------------------------------------------------------------------------ lecture (aucune écriture)
def _public(desk, row, now):
    apercu = json.loads(row['apercu'] or '{}')
    options = json.loads(row['options'] or '{}')
    actions = {'en_attente': ['modifier', 'maintenant', 'annuler'], 'echec': ['maintenant', 'retirer'], 'a_verifier': ['retirer'],
               'parti': ['retirer'], 'annule': ['retirer']}.get(row['etat'], [])
    motif = row['motif'] or ''
    return {'id': row['id'], 'version': row['version'], 'genre': row['genre'], 'etat': row['etat'], 'etat_libelle': ETATS.get(row['etat'], row['etat']),
            'depart': datetime.fromtimestamp(row['depart'], _zone(desk)).isoformat(timespec='minutes'), 'depart_libelle': libelle(desk, row['depart']),
            'prevu_libelle': libelle(desk, row['prevu']), 'reste': max(0, int(round(row['depart'] - now))) if row['etat'] == 'en_attente' else 0,
            'parti_libelle': libelle(desk, row['parti']) if row['parti'] else '', 'retard': bool(row['retard']),
            'a': apercu.get('a', ''), 'objet': apercu.get('objet', ''), 'destinataires': apercu.get('n', 0), 'pieces': apercu.get('pj', 0),
            'accuse': bool(options.get('accuse')), 'importance': bool(options.get('importance')),
            'motif': motif, 'motif_libelle': message(motif) if motif else '', 'actions': actions}


def liste(desk, owner, now=None):
    now = time.time() if now is None else now
    rows = desk.db.execute("SELECT * FROM envoi_file56106 WHERE owner=? AND masque=0 AND (etat IN ('en_attente','en_cours','echec','a_verifier') "
                           "OR (etat='parti' AND retard=1 AND parti>=?)) ORDER BY depart LIMIT 100", (str(owner or 'cabinet'), now - 7 * 86400)).fetchall()
    return {'elements': [_public(desk, r, now) for r in rows], 'delai': delai(desk, owner), 'delais': list(DELAIS), 'fuseau': fuseau(desk)}


def etat(desk, owner, ident, now=None):
    now = time.time() if now is None else now
    return _public(desk, _ligne(desk, owner, ident), now)


# ------------------------------------------------------------------------------------------------ départ (travailleur)
def _pause(desk):
    """Pause générale : bloque le départ (contrôle commun puis interrupteur « Pause » relu dans la base)."""
    from . import pause5629
    pause5629.checkpoint(desk)
    if desk.settings('missions567:pause', False):
        raise Stop('automatismes_en_pause')


def _attendre(desk, row, code, now):
    """Empêchement passager : le message reste en attente, le motif est affiché, nouvel essai plus tard."""
    desk.db.execute("UPDATE envoi_file56106 SET etat='en_attente', etape='', bail='', motif=?, prochain=?, maj=? WHERE id=? AND etat IN ('en_attente','en_cours')",
                    (code, now + ATTENTE, now, row['id']))
    desk.db.commit()


def _reserver(desk, row, bail, now):
    """Réservation atomique avant tout effet : un seul départ possible, même si deux passes se croisent."""
    from email.utils import make_msgid
    domain = str(desk.c['mail'].get('from_address') or 'localhost').rsplit('@', 1)[-1] or 'localhost'
    n = desk.db.execute("UPDATE envoi_file56106 SET etat='en_cours', etape='reserve', bail=?, message_id=?, version=version+1, maj=? "
                        "WHERE id=? AND etat='en_attente' AND version=?", (bail, make_msgid(domain=domain), now, row['id'], row['version'])).rowcount
    desk.db.commit()
    return n == 1


def _terminer(desk, ident, etat_, motif, now, retard=False):
    desk.db.execute('UPDATE envoi_file56106 SET etat=?, motif=?, bail=?, parti=?, retard=?, maj=? WHERE id=?',
                    (etat_, motif, '', now if etat_ == 'parti' else None, 1 if retard else 0, now, ident))
    desk.db.commit()


def _droits(desk, row):
    """Droits revus au départ : droit « envoyer » du compte et cloisonnement du brouillon (même contrôle qu'à la confirmation)."""
    from . import membres5650
    from .acces_courriel56100 import garde_envoi
    owner, role = row['owner'], row['role'] or 'avocat'
    try:
        if role not in ROLES or not membres5650.allowed(str(desk.c.get('state_dir') or ''), owner, role, 'POST', '/api440/' + PREFIXE + 'confirmer'):
            raise Stop('droit_envoi_retire')
        garde_envoi(desk, (owner, role), desk.c['mail'], {'uid': row['uid'], 'uidvalidity': row['validity']})
    except Stop as ex:
        if str(ex).split(':')[0] in ('introuvable', 'dossier_reserve', 'role_insuffisant'):
            raise Stop('droit_envoi_retire') from None
        raise


def partir(desk, row, bail, now=None):
    """Départ d'un message réservé par ``_reserver``. Rend l'état final (ou ``en_attente`` après un empêchement passager)."""
    from . import web490   # seule la couche web d'envoi touche au module d'envoi
    now = time.time() if now is None else now
    row = desk.db.execute('SELECT * FROM envoi_file56106 WHERE id=?', (row['id'],)).fetchone()
    options = json.loads(row['options'] or '{}')

    def on_log(log_id):
        desk.db.execute("UPDATE envoi_file56106 SET etape='smtp', log_id=? WHERE id=?", (log_id, row['id']))

    def on_sent():
        try:
            desk.db.execute("UPDATE envoi_file56106 SET etape='smtp_ok' WHERE id=?", (row['id'],))
            desk.db.commit()
        except sqlite3.Error:   # le départ a eu lieu : la suite ne doit jamais faire croire le contraire
            desk.db.rollback()

    try:
        _droits(desk, row)
        result = web490.file_depart(desk, row['uid'], row['validity'], row['fingerprint'], row['matter'], row['message_id'],
                                    en_tetes(options, options.get('de', '')), before_effect=lambda: _pause(desk), on_log=on_log, on_sent=on_sent)
    except Stop as ex:
        code = str(ex).split(':')[0]
        etape = (desk.db.execute('SELECT etape FROM envoi_file56106 WHERE id=?', (row['id'],)).fetchone() or ['reserve'])[0]
        code = 'brouillon_modifie_depuis_confirmation' if code in BROUILLON_CHANGE else code
        if etape == 'reserve' and (code in PASSAGERS or code.startswith('imap_')):
            _attendre(desk, row, code, now)
            return 'en_attente'
        if code in DOUTES:
            _terminer(desk, row['id'], 'a_verifier', code, now)
            return 'a_verifier'
        _terminer(desk, row['id'], 'echec', code, now)
        desk.audit('envoi56106_echec', {'motif': code})
        return 'echec'
    except (imaplib.IMAP4.error, OSError, sqlite3.Error, ValueError, LookupError) as ex:
        desk.db.rollback()
        etape = (desk.db.execute('SELECT etape FROM envoi_file56106 WHERE id=?', (row['id'],)).fetchone() or ['reserve'])[0]
        if etape == 'reserve':
            _attendre(desk, row, 'connexion_imap_echouee' if not isinstance(ex, sqlite3.Error) else 'controle_indisponible', now)
            return 'en_attente'
        _terminer(desk, row['id'], 'a_verifier', 'envoi_interrompu_a_verifier', now)
        return 'a_verifier'
    late = now - row['depart'] > RETARD
    _terminer(desk, row['id'], 'parti', '' if result.get('copy_saved') else 'copie_envoyes_non_enregistree', now, retard=late)
    desk.db.execute("UPDATE envoi_file56106 SET apercu=CASE WHEN ?=1 THEN apercu ELSE '{}' END WHERE id=?", (1 if late else 0, row['id']))
    desk.db.commit()
    desk.audit('envoi56106_parti', {'genre': row['genre'], 'retard': late, 'copie': bool(result.get('copy_saved'))})
    return 'parti'


def _dans_envoyes(desk, message_id):
    """True / False selon que le Message-ID est dans Envoyés ; None si la messagerie ne répond pas (on réessaiera)."""
    from .mailbox import Mailbox, imap_quote
    if not message_id:
        return False
    try:
        box = Mailbox(desk.c['mail'])
        try:
            return bool(box.search(desk.c['mail']['sent'], 'HEADER', 'Message-ID', imap_quote(message_id)))
        finally:
            box.close()
    except (Stop, imaplib.IMAP4.error, OSError):
        return None


def reprendre(desk, bail, now=None):
    """Messages restés « en cours » par un autre processus (redémarrage en plein envoi) : reprise PRUDENTE.
    - SMTP jamais commencé : le message repart normalement ;
    - SMTP commencé : on cherche le message dans Envoyés ; trouvé (ou SMTP réussi) = parti ; introuvable = « à vérifier », jamais renvoyé."""
    now = time.time() if now is None else now
    rows = desk.db.execute("SELECT * FROM envoi_file56106 WHERE etat='en_cours' AND bail<>?", (bail,)).fetchall()
    for row in rows:
        if row['etape'] in ('', 'reserve'):
            desk.db.execute("UPDATE envoi_file56106 SET etat='en_attente', etape='', bail='', motif='reprise_apres_interruption', version=version+1, maj=? "
                            "WHERE id=? AND etat='en_cours' AND bail=?", (now, row['id'], row['bail']))
            desk.db.commit()
            continue
        found = _dans_envoyes(desk, row['message_id'])
        if found is None:
            continue   # messagerie injoignable : rien n'est décidé, nouvel essai au prochain passage
        if found or row['etape'] == 'smtp_ok':
            status = 'envoye' if found else 'envoye_copie_manquante'
            if row['log_id']:
                desk.db.execute("UPDATE send_log490 SET status=?, detail=? WHERE id=? AND status='en_cours'",
                                (status, '' if found else 'copie_envoyes_non_enregistree', row['log_id']))
            desk.db.execute("UPDATE envoi_file56106 SET etat='parti', motif=?, bail='', parti=?, retard=?, maj=? WHERE id=? AND etat='en_cours' AND bail=?",
                            ('' if found else 'copie_envoyes_non_verifiee', now, 1 if now - row['depart'] > RETARD else 0, now, row['id'], row['bail']))
            desk.db.commit()
            try:   # le brouillon d'un message parti ne doit pas rester à portée d'un nouvel envoi
                from .drafts440 import discard_draft
                discard_draft(desk.c['mail'], row['uid'], row['validity'], 'yes')
            except Exception:   # noqa: BLE001 — déjà parti : le contrôle d'empreinte empêche de toute façon un second départ
                pass
            continue
        desk.db.execute("UPDATE envoi_file56106 SET etat='a_verifier', motif='envoi_interrompu_a_verifier', bail='', maj=? WHERE id=? AND etat='en_cours' AND bail=?",
                        (now, row['id'], row['bail']))
        desk.db.commit()
        desk.audit('envoi56106_a_verifier', {'genre': row['genre']})


def _purger(desk, now):
    desk.db.execute("DELETE FROM envoi_file56106 WHERE maj<? AND (etat IN ('parti','annule') OR masque=1)", (now - CONSERVATION,))
    desk.db.execute('DELETE FROM envoi_recus56106 WHERE cree<?', (now - 7 * 86400,))
    desk.db.commit()


def a_faire(desk, bail, now=None):
    now = time.time() if now is None else now
    return desk.db.execute("SELECT 1 FROM envoi_file56106 WHERE (etat='en_attente' AND depart<=? AND prochain<=?) OR (etat='en_cours' AND bail<>?) LIMIT 1",
                           (now, now, bail)).fetchone() is not None


def tick(desk, bail, now=None):
    """Une passe du travailleur : reprise prudente des départs interrompus, puis départ des messages dont l'heure est venue."""
    now = time.time() if now is None else now
    reprendre(desk, bail, now)
    rows = desk.db.execute("SELECT * FROM envoi_file56106 WHERE etat='en_attente' AND depart<=? AND prochain<=? ORDER BY depart LIMIT ?",
                           (now, now, PAR_PASSE)).fetchall()
    done = []
    for row in rows:
        try:
            _pause(desk)
        except Stop as ex:
            _attendre(desk, row, str(ex).split(':')[0], now)
            done.append((row['id'], 'en_attente'))
            continue
        if not _reserver(desk, row, bail, now):
            continue
        done.append((row['id'], partir(desk, row, bail, now)))
    return done


# ------------------------------------------------------------------------------------------------ fil du travailleur
def lane(c, stop, desk_factory=None):
    """Fil du worker 1 : départs de la file d'envoi, à la seconde. Toute erreur est journalisée et le fil continue."""
    bail = '%s-%s' % (time.strftime('%Y%m%d%H%M%S'), secrets.token_hex(6))
    desk = None
    last_purge = 0.0
    while not stop.is_set():
        try:
            if desk is None:
                if desk_factory:
                    desk = desk_factory(c)
                else:
                    from .desk import Desk
                    desk = Desk(c)
                desk.worker_id = 'envoi56106'
            if a_faire(desk, bail):
                if c.get('_path'):
                    from .ai_gateway import load_runtime_settings
                    from .common import load_config
                    desk.c = load_config(c['_path'])
                    load_runtime_settings(desk)
                tick(desk, bail)
            if time.monotonic() - last_purge > 3600:
                last_purge = time.monotonic()
                _purger(desk, time.time())
        except (sqlite3.Error, Stop):
            stop.wait(2)
        except Exception:   # noqa: BLE001 — le fil ne s'arrête jamais
            log.exception('file d’envoi 5.6.106')
            desk = None
            stop.wait(2)
        stop.wait(POLL)


def start_send_lane(c, stop):
    thread = threading.Thread(target=lane, args=(c, stop), daemon=True, name='envoi56106')
    thread.start()
    return thread


# ------------------------------------------------------------------------------------------------ routes
def allowed(role, method, api):
    """Contrôle de rôle sans état (``standalone_auth``) : avocat et administrateur ; le service revérifie droits, objet et version."""
    route = str(api or '').split('?', 1)[0][len(PREFIXE):]
    if method in ('GET', 'HEAD'):
        return route in ROUTES_GET and role in ROLES
    return method == 'POST' and route in ROUTES_POST and role in ROLES


def droit_requis(api, method):
    """Droit réglable du compte (``membres5650.category``) : tout effet sur un envoi relève du droit « envoyer » ; le délai est personnel."""
    route = str(api or '').split('?', 1)[0][len(PREFIXE):]
    if method in ('GET', 'HEAD') or route == 'reglage':
        return None
    return 'envoyer'


def _out(value, status='200 OK'):
    from .web440 import json_out
    return {**json_out(value, status), 'headers': [('Cache-Control', 'private, no-store')]}


def api(env, desk, auth, prefix, name, args, method):
    try:
        return _api(env, desk, auth, prefix, name, args, method)
    except Stop as ex:
        code = str(ex).split(':')[0]
        if code == 'dossier_reserve':
            code = 'introuvable'
        return _out({'error': code, 'message': message(code)}, STATUTS.get(code, '400 Bad Request'))
    except sqlite3.Error:
        return _out({'error': 'controle_indisponible', 'message': message('controle_indisponible')}, STATUTS['controle_indisponible'])


def _api(env, desk, auth, prefix, name, args, method):
    from .web440 import check_post
    from .web567 import actor
    owner, role = actor(env)
    owner = str(owner or 'cabinet')
    route = name.split('?', 1)[0][len(PREFIXE):]
    if route not in ROUTES_GET + ROUTES_POST:
        raise Stop('introuvable')
    if role not in ROLES:
        raise Stop('role_insuffisant')
    if method in ('GET', 'HEAD'):
        if route == 'liste':
            return _out(liste(desk, owner))
        if route == 'etat':
            return _out(etat(desk, owner, (args or {}).get('id')))
        raise Stop('introuvable')
    if method != 'POST' or route not in ROUTES_POST:
        raise Stop('introuvable')
    data = check_post(env, auth)
    cle = str(data.get('cle') or '')[:80]
    params = {k: data.get(k) for k in ('token', 'id', 'version', 'quand', 'accuse', 'importance', 'delai')}
    objet = hashlib.sha256(json.dumps({'r': route, 'p': params}, sort_keys=True, default=str).encode()).hexdigest()
    recu = (cle, owner, route, objet) if cle else None
    if cle:
        prior = desk.db.execute('SELECT objet, reponse FROM envoi_recus56106 WHERE owner=? AND cle=?', (owner, cle)).fetchone()
        if prior:
            if prior['objet'] != objet:
                raise Stop('cle_deja_utilisee')
            return _out(json.loads(prior['reponse']))
    if route == 'reglage':
        return _out(regler_delai(desk, owner, data.get('delai'), recu=recu))
    if route == 'confirmer':
        from .acces_courriel56100 import garde_envoi
        from .web490 import brouillon_du_jeton
        token = str(data.get('token') or '')
        garde_envoi(desk, (owner, role), desk.c['mail'], brouillon_du_jeton(desk, token))   # droits revus au moment de l'effet
        return _out(mettre_en_file(desk, owner, role, token, str(data.get('quand') or ''), data.get('accuse') is True,
                                   data.get('importance') is True, recu=recu))
    ident = str(data.get('id') or '')
    if route == 'annuler':
        return _out(annuler(desk, owner, ident, data.get('version'), recu=recu))
    if route == 'maintenant':
        return _out(maintenant(desk, owner, ident, data.get('version'), recu=recu))
    if route == 'modifier':
        return _out(modifier(desk, owner, ident, str(data.get('quand') or ''), data.get('accuse') is True, data.get('importance') is True,
                             data.get('version'), recu=recu))
    return _out(retirer(desk, owner, ident, recu=recu))
