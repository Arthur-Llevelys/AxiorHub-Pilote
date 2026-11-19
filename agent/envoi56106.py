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
  - plafond horaire (20) compté au départ réel ; le point de contrôle commun (``pause5629.checkpoint``) est relu avant le départ ;
    5.6.108 (lot P) : la suspension des envois programmés (et non plus la pause de l'IA) retient un départ programmé ;
  - droits revus au départ (droit « envoyer » du compte, cloisonnement du brouillon) ; brouillon relu et identique à la confirmation.

5.6.108 (lot C) :
  - B05 : le compte qui a confirmé est relu dans l'annuaire du mode d'installation juste avant le départ (``comptes56108``) : compte actif,
    rôle ACTUEL (jamais celui gardé en file), droit « envoyer », accès au dossier. Compte désactivé, introuvable, annuaire illisible ou
    identité non revérifiable : rien ne part, l'envoi est « retenu » avec son motif, le brouillon reste dans Brouillons ; seule une action
    explicite de la personne (« Envoyer maintenant ») le relance, après un nouveau contrôle. Réactiver un compte ne ressuscite rien.
  - B03 et B04 : rapprochement d'un départ « à vérifier » (acceptation partielle ou réponse SMTP perdue) : destinataires acceptés et
    refusés (code et date du retour), Message-ID, étapes de la tentative, recherche dans Envoyés (une absence ne prouve pas l'échec) ;
    décisions explicites de l'avocat seulement : « Renvoyer aux seuls destinataires refusés », « Je confirme qu'il est parti »,
    « Renvoyer quand même » (doublon possible annoncé). Jamais de reprise automatique ni globale.

5.6.108 (lot P) : la pause de l'IA ne retient plus les départs ; la **suspension des envois programmés** (``pause5629.checkpoint_envois``,
réglage distinct) les retient. Seuls les départs programmés sont retenus ; l'envoi immédiat explicite de l'avocat (délai d'annulation après
la confirmation, « Envoyer maintenant », décisions de rapprochement) reste possible, avec une confirmation de plus pendant la suspension.
Un départ programmé dont l'heure arrive pendant la suspension passe à l'état « suspendu » ; à la reprise, il attend une décision
(« Envoyer maintenant », « Reprogrammer », « Annuler ») : jamais de départ groupé automatique. Migration du réglage : ``ensure_schema``.
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
ROUTES_GET = ('liste', 'etat', 'rapprochement')
ROUTES_POST = ('confirmer', 'annuler', 'maintenant', 'modifier', 'retirer', 'reglage',
               'renvoyer_refuses', 'confirmer_parti', 'renvoyer_quand_meme',   # 5.6.108 (lot C) : décisions de rapprochement
               'suspendre')                                                     # 5.6.108 (lot P) : suspension des envois programmés
IMMEDIATS = ('maintenant', 'renvoyer_refuses', 'renvoyer_quand_meme')          # 5.6.108 (lot P) : envois immédiats explicites
ROLES = ('administrateur', 'avocat')
PASSAGERS = frozenset({'plafond_horaire_envois', 'automatismes_en_pause', 'ia_en_pause', 'controle_pause_indisponible',
                       'controle_suspension_indisponible',
                       'dossier_imap_inaccessible', 'uidvalidity_absent', 'connexion_imap_echouee', 'lecture_imap_echouee',
                       'imap_delai_depasse', 'imap_serveur_introuvable'})
DOUTES = frozenset({'envoi_incertain_verifiez_les_envoyes', 'smtp_envoi_incertain', 'smtp_envoi_partiel'})
BROUILLON_CHANGE = frozenset({'message_imap_disparu', 'uidvalidity_modifiee', 'message_non_brouillon', 'brouillon_modifie_depuis_apercu'})
COMPTE = frozenset({'auteur_non_verifiable', 'compte_desactive', 'compte_introuvable', 'annuaire_illisible', 'compte_non_verifiable',
                    'role_retire'})   # 5.6.108 (B05) : compte revu juste avant le départ → « retenu », jamais envoyé
INCERTAINS = frozenset({'smtp_envoi_incertain', 'envoi_interrompu_a_verifier', 'envoi_incertain_verifiez_les_envoyes'})

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
         'echec': 'Non envoyé', 'a_verifier': 'À vérifier', 'retenu': 'Retenu : compte à revérifier',
         'suspendu': 'Retenu par la suspension des envois'}   # 5.6.108 (lot P)
MESSAGES = {
    'smtp_envoi_incertain': 'Réponse SMTP perdue pendant la transmission : le résultat est incertain. Vérifiez avant tout nouvel envoi.',
    'smtp_envoi_partiel': 'Le serveur a accepté certains destinataires et en a refusé d’autres. Vérifiez le détail avant toute reprise.',
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
    # 5.6.108 (lot C) : compte revu au départ (B05) et rapprochement (B03, B04)
    'auteur_non_verifiable': 'Envoi sans auteur vérifiable : retenu, rien n’est parti. Le message est dans Brouillons.',
    'compte_desactive': 'Le compte qui a confirmé cet envoi a été désactivé depuis : rien n’est parti. Le message est resté dans Brouillons ; '
                        'un avocat autorisé peut le reprendre et l’envoyer sous sa propre confirmation.',
    'compte_introuvable': 'Le compte qui a confirmé cet envoi n’existe plus dans l’annuaire des comptes : rien n’est parti. Le message est '
                          'resté dans Brouillons.',
    'annuaire_illisible': 'L’annuaire des comptes est illisible : par prudence, rien n’est parti. Relancez avec « Envoyer maintenant » '
                          'quand il est de nouveau lisible.',
    'compte_non_verifiable': 'Votre compte (Nextcloud) ne peut pas être revérifié par AxiorHub à l’heure du départ : rien n’est parti. '
                             'Confirmez le départ avec « Envoyer maintenant ».',
    'role_retire': 'Le rôle actuel de ce compte ne permet plus d’envoyer : rien n’est parti.',
    'confirme_par_avocat': 'Départ confirmé par vous après vérification.',
    'rapprochement_impossible': 'Cette décision n’est pas possible pour cet envoi (déjà traité ou état différent) : la liste a été rechargée.',
    'aucun_destinataire_refuse': 'Aucun destinataire refusé n’est connu pour cet envoi : rien n’est renvoyé.',
    'relance_invalide': 'Le renvoi ne correspond plus aux destinataires refusés de la tentative d’origine : rien n’est parti.',
    'avertissement_doublon_requis': 'Cochez « Je sais qu’un doublon est possible » pour renvoyer ce message.',
    'smtp_destinataire_refuse': 'Le serveur d’envoi a refusé tous les destinataires : rien n’est parti. Corrigez les adresses puis réessayez.',
    'smtp_echec': 'Le serveur d’envoi a refusé le message avant sa transmission : rien n’est parti.',
    # 5.6.108 (lot P) : suspension des envois programmés, distincte de la pause de l'IA
    'envois_suspendus': 'Envois programmés suspendus : ce message n’est pas parti. À la reprise, vous déciderez : envoyer maintenant, '
                        'reprogrammer ou annuler.',
    'decision_apres_suspension': 'Son heure de départ est passée pendant la suspension des envois programmés : rien n’est parti. '
                                 'Choisissez : envoyer maintenant, reprogrammer ou annuler.',
    'controle_suspension_indisponible': 'État de la suspension des envois illisible : par prudence, rien ne part. Nouvel essai dans un instant.',
    'confirmation_suspension_requise': 'Les envois programmés sont suspendus : confirmez l’envoi immédiat de ce message.',
    'suspension_invalide': 'Demande illisible : indiquez s’il faut suspendre ou reprendre les envois programmés.',
}
STATUTS = {'introuvable': '404 Not Found', 'role_insuffisant': '403 Forbidden', 'version_en_conflit': '409 Conflict', 'deja_parti': '409 Conflict',
           'cle_deja_utilisee': '409 Conflict', 'deja_en_file': '409 Conflict', 'controle_indisponible': '503 Service Unavailable',
           'rapprochement_impossible': '409 Conflict', 'aucun_destinataire_refuse': '409 Conflict',
           'confirmation_suspension_requise': '409 Conflict', 'controle_suspension_indisponible': '503 Service Unavailable'}


JOURNAL = (('message_id', "TEXT NOT NULL DEFAULT ''"), ('destinataires', "TEXT NOT NULL DEFAULT ''"), ('decision', "TEXT NOT NULL DEFAULT ''"))


def ensure_schema(desk):
    """Au démarrage (``Desk._prepare``), jamais pendant une lecture. 5.6.108 (lot C) : auteur noté à la confirmation (B05) ; détail par
    destinataire, Message-ID et décision de l'avocat dans le journal des envois (B03, B04), colonnes ajoutées si le journal existe déjà."""
    desk.db.executescript(SCHEMA)
    if 'auteur' not in {r[1] for r in desk.db.execute('PRAGMA table_info(envoi_file56106)')}:
        desk.db.execute("ALTER TABLE envoi_file56106 ADD COLUMN auteur TEXT NOT NULL DEFAULT '{}'")
    colonnes = {r[1] for r in desk.db.execute('PRAGMA table_info(send_log490)')}
    if colonnes:   # journal absent : il est créé plus tard avec ces colonnes
        for nom, decl in JOURNAL:
            if nom not in colonnes:
                desk.db.execute('ALTER TABLE send_log490 ADD COLUMN %s %s' % (nom, decl))
    desk.db.commit()
    migrer_suspension(desk)


def migrer_suspension(desk, now=None):
    """5.6.108 (lot P) : migration du réglage, une seule fois, au démarrage (jamais pendant une lecture). Avant la 5.6.108, la pause de l'IA
    retenait aussi les départs. Pause posée par l'avocat : la suspension des envois programmés est posée (même effet) et les messages que
    la pause retenait déjà, heure passée, attendent une décision (« suspendu ») : aucune reprise silencieuse. Journal : ``envoi56108_migration``."""
    from . import pause5629
    now = time.time() if now is None else now
    rapport = pause5629.migrer_suspension_envois(desk, now)
    if rapport is None:
        desk.db.commit()
        return None
    anciens = ('automatismes_en_pause', 'ia_en_pause')
    retenus = 0
    if rapport['envois_suspendus']:
        retenus = desk.db.execute("UPDATE envoi_file56106 SET etat='suspendu', motif='envois_suspendus', prochain=0, version=version+1, maj=? "
                                  "WHERE etat='en_attente' AND depart<=? AND (motif IN (?,?) OR genre='programme')", (now, now, *anciens)).rowcount
    desk.db.execute("UPDATE envoi_file56106 SET motif='', version=version+1, maj=? WHERE etat='en_attente' AND motif IN (?,?)", (now, *anciens))
    rapport['retenus'] = retenus
    desk.db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)', (pause5629.MIGRATION_ENVOIS, json.dumps(rapport)))
    desk.db.commit()
    desk.audit('envoi56108_migration', rapport)
    return rapport


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
    from .comptes56108 import noter_auteur   # 5.6.108 (B05) : auteur fiable noté à la confirmation, revérifié juste avant le départ
    auteur = noter_auteur(desk, owner, role, now)
    desk.db.execute('INSERT INTO envoi_file56106(id,owner,role,uid,validity,fingerprint,matter,genre,prevu,depart,options,apercu,etat,cree,maj,auteur) '
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,'en_attente',?,?,?)",
                    (ident, owner, str(role or ''), info['uid'], info['validity'], info['fingerprint'], info['matter'], genre, depart, depart,
                     json.dumps(options), json.dumps(apercu, ensure_ascii=False), now, now, json.dumps(auteur)))
    out = {'file': True, 'id': ident, 'version': 1, 'genre': genre, 'delai': attente if genre == 'differe' else None,
           'depart': datetime.fromtimestamp(depart, _zone(desk)).isoformat(timespec='seconds'), 'depart_libelle': libelle(desk, depart),
           'reste': max(0, int(round(depart - now))), 'uid': info['uid'], 'uidvalidity': info['validity'],
           'options': {'accuse': options['accuse'], 'importance': options['importance']},
           'message': ('Envoi programmé le %s : le message reste dans Brouillons jusqu’à son départ.' % libelle(desk, depart)) if genre == 'programme'
           else ('Envoi dans %d s.' % attente if attente else 'Envoi en cours…')}
    if _suspension_active(desk):   # 5.6.108 (lot P) : l'état est annoncé ; seul un départ programmé est retenu
        out['suspension'] = True
        out['message'] += (' Les envois programmés sont suspendus : ce message attendra votre décision à la reprise.' if genre == 'programme'
                           else ' Les envois programmés sont suspendus ; cet envoi immédiat part quand même.')
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
                        "WHERE id=? AND owner=? AND etat IN ('en_attente','retenu','suspendu') AND version=?", (now, row['id'], row['owner'], row['version'])).rowcount
    if n != 1:
        desk.db.rollback()
        _refus(desk, owner, ident, ('en_attente', 'retenu', 'suspendu'))
    out = {'annule': True, 'uid': row['uid'], 'uidvalidity': row['validity'],
           'message': 'Envoi annulé : rien n’est parti, le message est resté dans Brouillons.'}
    _recu(desk, recu, out)
    desk.audit('envoi56106_annule', {'genre': row['genre']})
    return out


def maintenant(desk, owner, ident, version=None, now=None, recu=None, role=None):
    """« Envoyer maintenant » (ou « Réessayer » après un échec franc ; 5.6.108 : ou reprise explicite d'un envoi « retenu ») : départ
    avancé ; c'est toujours le travailleur qui envoie, après un nouveau contrôle du compte. Le geste de la personne, dans sa session
    (``role`` : rôle de la requête), renouvelle la preuve d'identité notée avec l'envoi."""
    now = time.time() if now is None else now
    row = _ligne(desk, owner, ident)
    if row['etat'] in ('en_cours', 'parti'):
        raise Stop('deja_parti')
    if row['etat'] not in ('en_attente', 'echec', 'retenu', 'suspendu'):
        raise Stop('action_impossible')
    _version(row, version)
    auteur = _auteur_geste(desk, row, role, now)
    options = _immediat(row)   # 5.6.108 (lot P) : envoi immédiat explicite : la suspension des envois programmés ne le retient pas
    n = desk.db.execute("UPDATE envoi_file56106 SET etat='en_attente', depart=?, prochain=0, motif='', auteur=?, options=?, version=version+1, maj=? "
                        "WHERE id=? AND owner=? AND etat IN ('en_attente','echec','retenu','suspendu') AND version=?",
                        (now, auteur, options, now, row['id'], row['owner'], row['version'])).rowcount
    if n != 1:
        desk.db.rollback()
        _refus(desk, owner, ident, ('en_attente', 'echec', 'retenu', 'suspendu'))
    out = {'id': row['id'], 'version': row['version'] + 1, 'message': 'Départ demandé : le message part dans un instant.'}
    _recu(desk, recu, out)
    desk.audit('envoi56106_maintenant', {'genre': row['genre'], 'reprise': row['etat'] in ('echec', 'retenu', 'suspendu')})
    return out


def modifier(desk, owner, ident, quand, accuse=False, importance=False, version=None, now=None, recu=None):
    """« Modifier » : nouvelle date et heure de départ, options. Pour changer le texte : annuler, puis reprendre le brouillon.
    5.6.108 (lot P) : « Reprogrammer » un envoi retenu par la suspension des envois programmés (état ``suspendu``) : il repart en attente
    à la nouvelle heure (et sera de nouveau retenu si la suspension est encore en vigueur à cette heure)."""
    now = time.time() if now is None else now
    row = _ligne(desk, owner, ident)
    if row['etat'] in ('en_cours', 'parti'):
        raise Stop('deja_parti')
    if row['etat'] not in ('en_attente', 'suspendu'):
        raise Stop('action_impossible')
    _version(row, version)
    depart = programme(desk, quand, now)
    options = {**_options(accuse, importance), 'de': json.loads(row['options'] or '{}').get('de', '')}
    options['accuse'] = options['accuse'] and bool(options['de'])
    n = desk.db.execute("UPDATE envoi_file56106 SET etat='en_attente', genre='programme', prevu=?, depart=?, prochain=0, motif='', options=?, "
                        "version=version+1, maj=? WHERE id=? AND owner=? AND etat IN ('en_attente','suspendu') AND version=?",
                        (depart, depart, json.dumps(options), now, row['id'], row['owner'], row['version'])).rowcount
    if n != 1:
        desk.db.rollback()
        _refus(desk, owner, ident, ('en_attente', 'suspendu'))
    out = {'id': row['id'], 'version': row['version'] + 1, 'depart_libelle': libelle(desk, depart),
           'message': 'Envoi programmé le %s.' % libelle(desk, depart)}
    _recu(desk, recu, out)
    desk.audit('envoi56106_modifie', {'options': sorted(k for k in ('accuse', 'importance') if options[k])})
    return out


def retirer(desk, owner, ident, now=None, recu=None):
    """Retire de la liste un envoi terminé ou à vérifier (aucun effet sur la messagerie)."""
    now = time.time() if now is None else now
    row = _ligne(desk, owner, ident)
    if row['etat'] not in ('echec', 'a_verifier', 'parti', 'annule', 'retenu'):
        raise Stop('action_impossible')
    desk.db.execute("UPDATE envoi_file56106 SET masque=1, apercu='{}', version=version+1, maj=? WHERE id=?", (now, row['id']))
    out = {'retire': True, 'message': 'Retiré de la liste.'}
    _recu(desk, recu, out)
    return out


def suspendre(desk, owner, actif, now=None, recu=None):
    """5.6.108 (lot P) : « Suspendre les envois programmés » / « Reprendre les envois programmés ». Réglage du cabinet, distinct de la pause
    de l'IA ; écrit avec ses effets dans une seule transaction.
    À la reprise : un départ programmé dont l'heure est passée pendant la suspension (déjà « suspendu », ou encore en attente si le
    travailleur n'est pas passé) reste « suspendu » et attend une décision explicite (« Envoyer maintenant », « Reprogrammer », « Annuler ») ;
    sa version change, si bien qu'une passe du travailleur commencée avant la reprise ne peut pas le réserver. Aucun départ groupé."""
    from . import pause5629
    if not isinstance(actif, bool):
        raise Stop('suspension_invalide')
    now = time.time() if now is None else now
    try:
        avant = pause5629.suspension_envois(desk) is not None
    except Stop:
        avant = True   # illisible : on réécrit le réglage, sans jamais rien faire partir
    if not desk.db.in_transaction:
        desk.db.execute('BEGIN IMMEDIATE')
    pause5629.ecrire_suspension_envois(desk, actif, owner, 'manuel', now)
    a_decider = 0
    if not actif:
        desk.db.execute("UPDATE envoi_file56106 SET etat='suspendu', motif='decision_apres_suspension', prochain=0, version=version+1, maj=? "
                        "WHERE etat='en_attente' AND genre='programme' AND depart<=? AND COALESCE(json_extract(options,'$.immediat'),0)<>1",
                        (now, now))
        desk.db.execute("UPDATE envoi_file56106 SET motif='decision_apres_suspension', version=version+1, maj=? WHERE etat='suspendu'", (now,))
        a_decider = desk.db.execute("SELECT COUNT(*) FROM envoi_file56106 WHERE etat='suspendu' AND owner=?", (str(owner or 'cabinet'),)).fetchone()[0]
    if actif:
        message_ = ('Envois programmés suspendus : aucun départ programmé ne partira avant votre reprise. Les envois immédiats que vous '
                    'confirmez restent possibles. La pause de l’IA est une commande distincte.')
    elif a_decider:
        message_ = ('Envois programmés repris. %d message(s) dont l’heure est passée pendant la suspension attendent votre décision dans '
                    '« Envois programmés » : rien n’est parti automatiquement.' % a_decider)
    else:
        message_ = 'Envois programmés repris : les prochains départs auront lieu à leur heure.'
    out = {'actif': actif, 'a_decider': a_decider, 'message': message_}
    _recu(desk, recu, out)
    desk.audit('envoi56108_suspension', {'actif': actif, 'avant': avant, 'a_decider': a_decider, 'owner': str(owner or 'cabinet')[:120]})
    return out


def _recu(desk, recu, out):
    """Clé d'idempotence : le reçu est écrit dans la même transaction que l'effet, puis tout est validé."""
    if recu and recu[0]:
        cle, owner, route, objet = recu
        desk.db.execute('INSERT OR REPLACE INTO envoi_recus56106 VALUES(?,?,?,?,?,?)',
                        (cle, owner, route, objet, json.dumps(out, ensure_ascii=False, default=str), time.time()))
    desk.db.commit()


# ------------------------------------------------------------------------------------------------ lecture (aucune écriture)
def _public(desk, row, now, suspension=None):
    apercu = json.loads(row['apercu'] or '{}')
    options = json.loads(row['options'] or '{}')
    actions = {'en_attente': ['modifier', 'maintenant', 'annuler'], 'echec': ['maintenant', 'retirer'], 'a_verifier': ['retirer'],
               'parti': ['retirer'], 'annule': ['retirer'], 'retenu': ['maintenant', 'annuler', 'retirer'],
               'suspendu': ['maintenant', 'reprogrammer', 'annuler']}.get(row['etat'], [])   # 5.6.108 (lot P) : décision, jamais de retrait
    motif = row['motif'] or ''
    if row['etat'] == 'suspendu' and suspension is False:
        motif = 'decision_apres_suspension'   # 5.6.108 (lot P) : la suspension est levée ; heure passée : à décider
    return {'id': row['id'], 'version': row['version'], 'genre': row['genre'], 'etat': row['etat'], 'etat_libelle': ETATS.get(row['etat'], row['etat']),
            'depart': datetime.fromtimestamp(row['depart'], _zone(desk)).isoformat(timespec='minutes'), 'depart_libelle': libelle(desk, row['depart']),
            'prevu_libelle': libelle(desk, row['prevu']), 'reste': max(0, int(round(row['depart'] - now))) if row['etat'] == 'en_attente' else 0,
            'parti_libelle': libelle(desk, row['parti']) if row['parti'] else '', 'retard': bool(row['retard']),
            'a': apercu.get('a', ''), 'objet': apercu.get('objet', ''), 'destinataires': apercu.get('n', 0), 'pieces': apercu.get('pj', 0),
            'accuse': bool(options.get('accuse')), 'importance': bool(options.get('importance')),
            'motif': motif, 'motif_libelle': message(motif) if motif else '', 'actions': actions,
            'rapprochement': row['etat'] == 'a_verifier',   # 5.6.108 (B03, B04) : écran « Vérifier et décider »
            # 5.6.108 (lot P) : retenu (ou à retenir à son heure) par la suspension des envois programmés
            'suspension': row['etat'] == 'suspendu' or (bool(suspension) and row['etat'] == 'en_attente' and row['genre'] == 'programme'
                                                        and not options.get('immediat'))}


def _suspension_lue(desk):
    """Pour l'affichage : (état public, actif) ; un réglage illisible s'affiche comme tel (et retient les départs), jamais comme levé."""
    from . import pause5629
    try:
        value = pause5629.suspension_envois(desk)
    except Stop:
        return {'actif': True, 'illisible': True, 'texte': message('controle_suspension_indisponible')}, True
    if not value:
        return {'actif': False, 'texte': 'Envois programmés actifs.'}, False
    depuis = value.get('depuis')
    texte = ('Envois programmés suspendus depuis le %s.' % libelle(desk, depuis)) if isinstance(depuis, (int, float)) else 'Envois programmés suspendus.'
    if value.get('origine') == 'migration':
        texte += ' La pause de l’IA était active lors de la mise à jour : la suspension a été conservée jusqu’à votre décision.'
    return {'actif': True, 'origine': str(value.get('origine') or ''), 'texte': texte}, True


def _suspension_active(desk):
    return _suspension_lue(desk)[1]


def liste(desk, owner, now=None):
    now = time.time() if now is None else now
    etat_suspension, actif = _suspension_lue(desk)
    rows = desk.db.execute("SELECT * FROM envoi_file56106 WHERE owner=? AND masque=0 AND (etat IN ('en_attente','en_cours','echec','a_verifier','retenu','suspendu') "
                           "OR (etat='parti' AND retard=1 AND parti>=?)) ORDER BY depart LIMIT 100", (str(owner or 'cabinet'), now - 7 * 86400)).fetchall()
    elements = [_public(desk, r, now, actif) for r in rows]
    etat_suspension['retenus'] = sum(1 for x in elements if x['etat'] == 'suspendu')
    return {'elements': elements, 'delai': delai(desk, owner), 'delais': list(DELAIS), 'fuseau': fuseau(desk),
            'suspension': etat_suspension, 'pause_ia': bool(desk.settings('missions567:pause', False))}


def etat(desk, owner, ident, now=None):
    now = time.time() if now is None else now
    return _public(desk, _ligne(desk, owner, ident), now, _suspension_active(desk))


# ------------------------------------------------------------------- 5.6.108 (lot C, B03 et B04) : rapprochement d'un départ « à vérifier »
ETATS_DESTINATAIRE = {'accepte': 'Accepté par le serveur d’envoi', 'refuse': 'Refusé par le serveur d’envoi',
                      'inconnu': 'Résultat inconnu (réponse perdue)', 'non_transmis': 'Non transmis'}
NOTE_ACCEPTATION = ('« Accepté » veut dire pris en charge par le serveur d’envoi : ce n’est ni la réception finale par le destinataire, '
                    'ni sa lecture.')


def _journal(desk, row):
    if not row['log_id']:
        return None
    return desk.db.execute('SELECT * FROM send_log490 WHERE id=?', (row['log_id'],)).fetchone()


def _destinataires(log):
    try:
        data = json.loads(log['destinataires'] or '[]') if log is not None and 'destinataires' in log.keys() else []
    except ValueError:
        data = []
    return [x for x in data if isinstance(x, dict) and x.get('adresse')] if isinstance(data, list) else []


def _libelle_iso(desk, texte):
    try:
        return libelle(desk, datetime.fromisoformat(str(texte)).timestamp()) if texte else ''
    except ValueError:
        return ''


def _nature(row):
    return 'partiel' if row['motif'] == 'smtp_envoi_partiel' else 'incertain'


def rapprochement(desk, owner, ident, now=None):
    """Écran « À vérifier » (lecture seule) : destinataires acceptés et refusés avec le code et la date du retour SMTP, Message-ID, étapes de
    la tentative, recherche du Message-ID dans Envoyés, décisions possibles. Aucune écriture, aucun nouvel envoi."""
    now = time.time() if now is None else now
    row = _ligne(desk, owner, ident)
    if row['etat'] != 'a_verifier':
        raise Stop('rapprochement_impossible')
    log = _journal(desk, row)
    dest = [{'adresse': str(x.get('adresse'))[:320], 'etat': x.get('etat') if x.get('etat') in ETATS_DESTINATAIRE else 'inconnu',
             'etat_libelle': ETATS_DESTINATAIRE.get(x.get('etat'), ETATS_DESTINATAIRE['inconnu']), 'code': x.get('code'),
             'reponse': str(x.get('reponse') or '')[:200], 'date_libelle': _libelle_iso(desk, x.get('date'))} for x in _destinataires(log)]
    trace = [{'etape': 'Envoi confirmé par vous', 'le': libelle(desk, row['cree'])},
             {'etape': 'Départ prévu', 'le': libelle(desk, row['prevu'])}]
    if log is not None:
        trace.append({'etape': 'Transmission au serveur d’envoi commencée', 'le': _libelle_iso(desk, log['at'])})
    trace.append({'etape': message(row['motif']) if row['motif'] else ETATS['a_verifier'], 'le': libelle(desk, row['maj'])})
    trouve = _dans_envoyes(desk, row['message_id']) if row['message_id'] else False
    recherche = {'trouve': trouve, 'texte': (
        'Message retrouvé dans Envoyés (même Message-ID).' if trouve else
        'Recherche impossible pour le moment : la messagerie ne répond pas.' if trouve is None else
        'Message absent d’Envoyés. Une absence ne prouve pas l’échec : la copie dans Envoyés n’est faite qu’après un départ complet.')}
    nature = _nature(row)
    refuses = [x for x in dest if x['etat'] == 'refuse']
    decisions = (['renvoyer_refuses'] if refuses else []) + ['confirmer_parti'] if nature == 'partiel' else ['confirmer_parti', 'renvoyer_quand_meme']
    return {'id': row['id'], 'version': row['version'], 'nature': nature, 'objet': json.loads(row['apercu'] or '{}').get('objet', ''),
            'motif': row['motif'], 'motif_libelle': message(row['motif']) if row['motif'] else '', 'message_id': row['message_id'],
            'destinataires': dest, 'acceptes': sum(1 for x in dest if x['etat'] == 'accepte'), 'refuses': len(refuses),
            'trace': trace, 'envoyes': recherche, 'note': NOTE_ACCEPTATION, 'decisions': decisions,
            'doublon': 'Renvoyer quand même peut faire recevoir le message deux fois si le premier départ a abouti.' if nature == 'incertain' else ''}


def _a_verifier(desk, owner, ident, version):
    row = _ligne(desk, owner, ident)
    if row['etat'] != 'a_verifier':
        raise Stop('rapprochement_impossible')
    _version(row, version)
    return row


def _sans_adresses(texte):
    try:
        data = json.loads(texte or '[]')
    except ValueError:
        return ''
    return json.dumps([{k: v for k, v in x.items() if k in ('etat', 'code', 'date')} for x in data if isinstance(x, dict)]
                      if isinstance(data, list) else [], ensure_ascii=False)


def _decider(desk, log_id, action, owner, now, statut=None, depuis=()):
    """Décision de l'avocat notée au journal ; une décision qui clôt le rapprochement retire les adresses du retour par destinataire."""
    decision = json.dumps({'action': action, 'par': str(owner)[:120], 'le': datetime.fromtimestamp(now, timezone.utc).isoformat(timespec='seconds')})
    if not log_id:
        return
    if statut:
        marks = ','.join('?' * len(depuis))
        row = desk.db.execute('SELECT destinataires FROM send_log490 WHERE id=?', (log_id,)).fetchone()
        desk.db.execute('UPDATE send_log490 SET status=?, decision=?, destinataires=? WHERE id=? AND status IN (%s)' % marks,
                        (statut, decision, _sans_adresses(row[0] if row else ''), log_id, *depuis))
    else:
        desk.db.execute('UPDATE send_log490 SET decision=? WHERE id=?', (decision, log_id))


def _relancer(desk, row, options, auteur, now):
    options = {**options, 'immediat': True}   # 5.6.108 (lot P) : décision explicite de l'avocat : envoi immédiat, hors suspension
    n = desk.db.execute("UPDATE envoi_file56106 SET etat='en_attente', depart=?, prochain=0, motif='', options=?, auteur=?, version=version+1, maj=? "
                        "WHERE id=? AND etat='a_verifier' AND version=?",
                        (now, json.dumps(options), auteur, now, row['id'], row['version'])).rowcount
    if n != 1:
        desk.db.rollback()
        raise Stop('version_en_conflit')


def _auteur_geste(desk, row, role, now):
    """Geste explicite de la personne dans sa session : preuve d'identité renouvelée. Un annuaire local noté à la confirmation reste celui
    qui fait foi (un compte retiré de l'annuaire n'est jamais repris comme identité de session)."""
    if role is None:
        return row['auteur'] if 'auteur' in row.keys() else '{}'
    from .comptes56108 import noter_auteur
    nouveau = noter_auteur(desk, row['owner'], role, now)
    ancien = auteur_de(row)
    if ancien.get('profil') in ('installation', 'serveur', 'autonome'):
        nouveau['profil'] = ancien['profil']
    return json.dumps(nouveau)


def renvoyer_refuses(desk, owner, ident, version=None, now=None, recu=None, role=None):
    """B03 : « Renvoyer aux seuls destinataires refusés » : décision explicite ; le même message (même Message-ID) repart, par le
    travailleur, vers les seules adresses refusées lors de la tentative partielle ; les destinataires acceptés ne reçoivent rien de plus."""
    now = time.time() if now is None else now
    row = _a_verifier(desk, owner, ident, version)
    log = _journal(desk, row)
    refuses = [x['adresse'] for x in _destinataires(log) if x.get('etat') == 'refuse']
    if _nature(row) != 'partiel' or log is None or log['status'] != 'partiel':
        raise Stop('rapprochement_impossible')
    if not refuses:
        raise Stop('aucun_destinataire_refuse')
    options = {k: v for k, v in json.loads(row['options'] or '{}').items() if k not in ('seuls', 'relance_de', 'message_id_origine')}
    options.update(seuls=refuses, relance_de=log['id'], message_id_origine=row['message_id'])
    _relancer(desk, row, options, _auteur_geste(desk, row, role, now), now)
    _decider(desk, log['id'], 'renvoyer_refuses', owner, now)
    out = {'id': row['id'], 'version': row['version'] + 1, 'destinataires': refuses,
           'message': 'Renvoi demandé aux seuls destinataires refusés (%d) : le message part dans un instant.' % len(refuses)}
    _recu(desk, recu, out)
    desk.audit('envoi56108_renvoi_refuses', {'destinataires': len(refuses)})
    return out


def confirmer_parti(desk, owner, ident, version=None, now=None, recu=None):
    """B03, B04 : « Je confirme qu'il est parti » : l'avocat a vérifié (Envoyés, destinataire) ; l'envoi est clos, le journal le compte comme
    envoyé (un même message ne pourra pas repartir par erreur). Aucun effet sur la messagerie."""
    now = time.time() if now is None else now
    row = _a_verifier(desk, owner, ident, version)
    n = desk.db.execute("UPDATE envoi_file56106 SET etat='parti', motif='confirme_par_avocat', parti=?, version=version+1, maj=? "
                        "WHERE id=? AND etat='a_verifier' AND version=?", (now, now, row['id'], row['version'])).rowcount
    if n != 1:
        desk.db.rollback()
        raise Stop('version_en_conflit')
    _decider(desk, row['log_id'], 'confirmer_parti', owner, now, 'envoye_confirme', ('a_verifier', 'partiel', 'en_cours'))
    out = {'id': row['id'], 'version': row['version'] + 1,
           'message': 'Départ confirmé par vous. Le brouillon reste dans Brouillons : retirez-le si vous n’en avez plus besoin.'}
    _recu(desk, recu, out)
    desk.audit('envoi56108_confirme_parti', {'nature': _nature(row)})
    return out


def renvoyer_quand_meme(desk, owner, ident, version=None, doublon=False, now=None, recu=None, role=None):
    """B04 : « Renvoyer quand même » après un résultat incertain : seulement si l'avocat coche l'avertissement de doublon possible ; jamais
    après une acceptation partielle (les destinataires acceptés le recevraient deux fois : « Renvoyer aux seuls destinataires refusés »)."""
    now = time.time() if now is None else now
    if doublon is not True:
        raise Stop('avertissement_doublon_requis')
    row = _a_verifier(desk, owner, ident, version)
    if _nature(row) != 'incertain' or desk.db.execute("SELECT 1 FROM send_log490 WHERE fingerprint=? AND status='partiel'",
                                                      (row['fingerprint'],)).fetchone():
        raise Stop('rapprochement_impossible')
    options = {k: v for k, v in json.loads(row['options'] or '{}').items() if k not in ('seuls', 'relance_de', 'message_id_origine')}
    _relancer(desk, row, options, _auteur_geste(desk, row, role, now), now)
    decision = json.dumps({'action': 'renvoyer_quand_meme', 'par': str(owner)[:120], 'le': datetime.fromtimestamp(now, timezone.utc).isoformat(timespec='seconds')})
    for (log_id, texte) in desk.db.execute("SELECT id, destinataires FROM send_log490 WHERE fingerprint=? AND status IN ('a_verifier','en_cours')",
                                           (row['fingerprint'],)).fetchall():
        desk.db.execute("UPDATE send_log490 SET status='a_verifier_renvoye', decision=?, destinataires=? WHERE id=?",
                        (decision, _sans_adresses(texte), log_id))
    out = {'id': row['id'], 'version': row['version'] + 1, 'message': 'Nouvel envoi demandé par vous : le message part dans un instant.'}
    _recu(desk, recu, out)
    desk.audit('envoi56108_renvoi_quand_meme', {'motif': row['motif']})
    return out


# ------------------------------------------------------------------------------------------------ départ (travailleur)
def _pause(desk):
    """Point de contrôle commun (travail ou automatisme en cours : arrêt, plage horaire) relu avant le départ.
    5.6.108 (lot P) : la pause de l'IA (``missions567:pause``) ne retient plus un envoi que l'avocat a validé ; voir ``_suspension``."""
    from . import pause5629
    pause5629.checkpoint(desk)


def _immediat(row):
    """Options d'un envoi devenu immédiat par un geste explicite de l'avocat (JSON prêt à écrire)."""
    try:
        options = json.loads(row['options'] or '{}')
    except ValueError:
        options = {}
    return json.dumps({**(options if isinstance(options, dict) else {}), 'immediat': True})


def _suspension(desk, row):
    """5.6.108 (lot P) : suspension des envois programmés relue dans la base, avant la réservation puis juste avant le SMTP. Retient un départ
    programmé ; un envoi immédiat (délai d'annulation après la confirmation, ou geste explicite « Envoyer maintenant ») n'est pas concerné."""
    try:
        options = json.loads(row['options'] or '{}')
    except ValueError:
        options = {}
    if row['genre'] != 'programme' or (isinstance(options, dict) and options.get('immediat') is True):
        return
    from . import pause5629
    pause5629.checkpoint_envois(desk)


def _retenir(desk, row, now):
    """Départ programmé retenu par la suspension : état « suspendu », il attendra une décision de l'avocat (jamais de départ groupé)."""
    desk.db.execute("UPDATE envoi_file56106 SET etat='suspendu', etape='', bail='', motif='envois_suspendus', prochain=0, version=version+1, maj=? "
                    "WHERE id=? AND etat IN ('en_attente','en_cours')", (now, row['id']))
    desk.db.commit()


def _attendre(desk, row, code, now):
    """Empêchement passager : le message reste en attente, le motif est affiché, nouvel essai plus tard."""
    desk.db.execute("UPDATE envoi_file56106 SET etat='en_attente', etape='', bail='', motif=?, prochain=?, maj=? WHERE id=? AND etat IN ('en_attente','en_cours')",
                    (code, now + ATTENTE, now, row['id']))
    desk.db.commit()


def _reserver(desk, row, bail, now):
    """Réservation atomique avant tout effet : un seul départ possible, même si deux passes se croisent."""
    from email.utils import make_msgid
    domain = str(desk.c['mail'].get('from_address') or 'localhost').rsplit('@', 1)[-1] or 'localhost'
    try:   # 5.6.108 (B03) : renvoi aux seuls refusés : même message, même Message-ID que pour les destinataires déjà acceptés
        origine = str(json.loads(row['options'] or '{}').get('message_id_origine') or '')
    except ValueError:
        origine = ''
    n = desk.db.execute("UPDATE envoi_file56106 SET etat='en_cours', etape='reserve', bail=?, message_id=?, version=version+1, maj=? "
                        "WHERE id=? AND etat='en_attente' AND version=?", (bail, origine or make_msgid(domain=domain), now, row['id'], row['version'])).rowcount
    desk.db.commit()
    return n == 1


def _terminer(desk, ident, etat_, motif, now, retard=False):
    desk.db.execute('UPDATE envoi_file56106 SET etat=?, motif=?, bail=?, parti=?, retard=?, maj=? WHERE id=?',
                    (etat_, motif, '', now if etat_ == 'parti' else None, 1 if retard else 0, now, ident))
    desk.db.commit()


def auteur_de(row):
    """Auteur noté à la confirmation (5.6.108) ; envoi mis en file avant la 5.6.108 : propriétaire seul, sans preuve de session."""
    try:
        auteur = json.loads(row['auteur'] or '{}') if 'auteur' in row.keys() else {}
    except ValueError:
        auteur = {}
    return auteur if isinstance(auteur, dict) and auteur.get('owner') == row['owner'] else {'owner': row['owner']}


def _droits(desk, row, now=None):
    """Droits revus au départ : 5.6.108 (B05) compte relu dans l'annuaire du mode d'installation (actif, rôle ACTUEL ; le rôle gardé en file
    n'est jamais utilisé), puis droit « envoyer » du compte avec ce rôle et cloisonnement du brouillon (même contrôle qu'à la confirmation).
    Motifs de compte (``COMPTE``) : l'envoi est retenu ; droit ou dossier retirés : ``droit_envoi_retire``."""
    from .acces_courriel56100 import garde_envoi
    try:
        compte = _compte(desk, row, now)
        garde_envoi(desk, (compte['owner'], compte['role']), desk.c['mail'], {'uid': row['uid'], 'uidvalidity': row['validity']})
    except Stop as ex:
        if str(ex).split(':')[0] in ('introuvable', 'dossier_reserve', 'role_insuffisant'):
            raise Stop('droit_envoi_retire') from None
        raise


def _compte(desk, row, now=None):
    """Compte, rôle actuel, droit « envoyer » et dossier (sans lecture IMAP) : relu au début du départ puis juste avant le SMTP."""
    from .comptes56108 import verifier_auteur
    try:
        return verifier_auteur(desk, auteur_de(row), roles=ROLES, methode='POST', chemin='/api440/' + PREFIXE + 'confirmer',
                               dossier=row['matter'] or None, maintenant=now)
    except Stop as ex:
        if str(ex).split(':')[0] in ('droit_retire', 'dossier_reserve'):
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

    def avant_effet():   # juste avant le SMTP : contrôle commun, suspension des envois programmés (lot P), puis compte relu (B05)
        _pause(desk)
        _suspension(desk, row)
        _compte(desk, row, now)

    relance = {k: options[k] for k in ('seuls', 'relance_de') if options.get(k)}   # 5.6.108 (B03) : seuls destinataires refusés
    try:
        _droits(desk, row, now)
        result = web490.file_depart(desk, row['uid'], row['validity'], row['fingerprint'], row['matter'], row['message_id'],
                                    en_tetes(options, options.get('de', '')), before_effect=avant_effet, on_log=on_log, on_sent=on_sent,
                                    **relance)
    except Stop as ex:
        code = str(ex).split(':')[0]
        etape = (desk.db.execute('SELECT etape FROM envoi_file56106 WHERE id=?', (row['id'],)).fetchone() or ['reserve'])[0]
        code = 'brouillon_modifie_depuis_confirmation' if code in BROUILLON_CHANGE else code
        if etape == 'reserve' and code == 'envois_suspendus':   # 5.6.108 (lot P) : suspension posée entre la réservation et le SMTP
            _retenir(desk, row, now)
            return 'suspendu'
        if etape == 'reserve' and (code in PASSAGERS or code.startswith('imap_')):
            _attendre(desk, row, code, now)
            return 'en_attente'
        if etape == 'reserve' and code in COMPTE:   # 5.6.108 (B05) : rien n'est parti ; reprise explicite seulement
            _terminer(desk, row['id'], 'retenu', code, now)
            desk.audit('envoi56106_retenu', {'motif': code})
            return 'retenu'
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
            _suspension(desk, row)   # 5.6.108 (lot P) : départ programmé retenu par la suspension des envois programmés
        except Stop as ex:
            code = str(ex).split(':')[0]
            if code == 'envois_suspendus':
                _retenir(desk, row, now)
                done.append((row['id'], 'suspendu'))
                continue
            _attendre(desk, row, code, now)
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


# ------------------------------------------------------------------------------------------------ en-tête d'Aujourd'hui (lot P)
def commande_html(desk, prefix, owner):
    """5.6.108 (lot P) : commande « Suspendre les envois programmés » et son état, à côté de « Mettre l'IA en pause » dans l'en-tête
    d'Aujourd'hui ; nombre de messages de ce compte retenus par la suspension, avec un lien vers « Envois programmés ». Lecture seule.
    Le clic est géré par ``v56106-envoi.js`` (confirmation, clé d'idempotence)."""
    from html import escape as e
    etat_, actif = _suspension_lue(desk)
    try:
        retenus = desk.db.execute("SELECT COUNT(*) FROM envoi_file56106 WHERE owner=? AND masque=0 AND etat='suspendu'",
                                  (str(owner or 'cabinet'),)).fetchone()[0]
    except sqlite3.Error:
        retenus = 0
    bouton = ('<button type="button" class="ax-btn %s p108-envois" id="p108-envois" data-actif="%s" aria-pressed="%s" title="%s">%s</button>' % (
        'warn' if actif else 'ghost', '1' if actif else '0', 'true' if actif else 'false', e(etat_['texte'], quote=True),
        e('Envois programmés suspendus — reprendre' if actif else 'Suspendre les envois programmés')))
    lien = '<a class="p108-retenus" id="p108-retenus" href="%s"%s>%s</a>' % (
        e(prefix + '/courriels?vue=boite#envois-programmes', quote=True), '' if retenus else ' hidden', e('%d envoi(s) retenu(s) à décider' % retenus))
    return bouton + lien


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
        if route == 'rapprochement':   # 5.6.108 (B03, B04) : lecture seule (journal, recherche dans Envoyés), aucune écriture
            return _out(rapprochement(desk, owner, (args or {}).get('id')))
        raise Stop('introuvable')
    if method != 'POST' or route not in ROUTES_POST:
        raise Stop('introuvable')
    data = check_post(env, auth)
    cle = str(data.get('cle') or '')[:80]
    params = {k: data.get(k) for k in ('token', 'id', 'version', 'quand', 'accuse', 'importance', 'delai', 'doublon', 'actif', 'hors_suspension')}
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
    if route == 'suspendre':   # 5.6.108 (lot P) : suspension des envois programmés (réglage du cabinet, distinct de la pause de l'IA)
        return _out(suspendre(desk, owner, data.get('actif'), recu=recu))
    if route in IMMEDIATS and data.get('hors_suspension') is not True:
        from . import pause5629   # 5.6.108 (lot P) : pendant la suspension, un envoi immédiat demande une confirmation de plus
        if pause5629.suspension_envois(desk):
            raise Stop('confirmation_suspension_requise')
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
        return _out(maintenant(desk, owner, ident, data.get('version'), recu=recu, role=role))
    if route == 'renvoyer_refuses':   # 5.6.108 (B03, B04) : décisions explicites de rapprochement
        return _out(renvoyer_refuses(desk, owner, ident, data.get('version'), recu=recu, role=role))
    if route == 'confirmer_parti':
        return _out(confirmer_parti(desk, owner, ident, data.get('version'), recu=recu))
    if route == 'renvoyer_quand_meme':
        return _out(renvoyer_quand_meme(desk, owner, ident, data.get('version'), data.get('doublon') is True, recu=recu, role=role))
    if route == 'modifier':   # 5.6.108 (lot P) : aussi « Reprogrammer » un envoi retenu par la suspension
        return _out(modifier(desk, owner, ident, str(data.get('quand') or ''), data.get('accuse') is True, data.get('importance') is True,
                             data.get('version'), recu=recu))
    return _out(retirer(desk, owner, ident, recu=recu))
