"""5.6.97 (lot gmail, cahier v3 C03-C07) : fournisseur Gmail derrière le contrat de boîte de la 5.6.96 (``courriels5696``).

- **Deux réglages distincts** (C07) : la vue « À traiter avec le Pilote » (5.6.96) et ce fournisseur, **désactivé par défaut** (cabinet,
  administrateur). Désactiver le fournisseur rend l'usage courant (IMAP) sans rien effacer : connexions, projets, suivis et preuves restent.
  Un échec Google ne touche ni IMAP ni les autres comptes : chaque connexion est celle d'une personne (``google5697``, usage ``gmail``).
- **Lecture** (``gmail.readonly``) : ``FournisseurGmail`` expose seulement ``lister``, ``lire_message``, ``lire_fil`` et ``lire_piece`` ; les trois
  premières lisent la projection locale (aucun appel Google au rendu), la pièce est lue à la demande. Référence d'un message :
  (connexion, identifiant Gmail) ; ``threadId`` groupe le fil ; jamais de fusion par objet ni entre comptes.
- **Synchronisation hors de la requête de page** (travail ``gmail5697``) : import initial borné (7 jours, 250 messages ; « couverture
  partielle » si le plafond est atteint), puis ``history.list`` incrémental paginé ; le point de reprise n'est enregistré qu'après
  l'écriture durable de la série (écritures idempotentes). 404 sur le curseur : resynchronisation bornée et fenêtre incomplète signalée.
  429 et 5xx : reprise espacée (``Retry-After`` ou attente croissante), jamais de boucle ; autorisation retirée (``invalid_grant``) : arrêt.
  Un seul travail de lecture actif par boîte (bail). Rien n'est jamais écrit dans Gmail par la synchronisation.
- **Qualifier avant toute préparation** (C04) : courrier envoyé, copie à soi, notifications, listes, catégories promotionnelles et
  indésirables ne sont jamais « à traiter ». Un projet local (« Préparer une réponse ») est une commande de l'avocat, sans IA.
- **Brouillon dans Gmail** (``gmail.compose``, consentement additionnel à la demande) : MIME en base64url, ``drafts.create`` dans le fil
  (``threadId``, ``In-Reply-To``, ``References``, objet cohérent), relu après dépôt ; une modification distante est signalée, jamais écrasée.
- **Envoi** : seulement « Envoyer maintenant » après relecture exacte (compte d'expédition, À, Cc, objet, texte, pièces) ; opération unique
  réservée en transaction (clé d'idempotence, version, empreinte, génération du compte) ; ``drafts.send`` une seule fois. Réponse perdue
  ou 5xx : « Envoi à vérifier », verrouillé, preuve cherchée dans les envoyés par l'identifiant du message ; **jamais de renvoi automatique**.
  L'agent n'a pas cette capacité : aucune méthode du fournisseur n'écrit, et l'envoi est refusé dans un travail ou un automatisme.
"""
import base64
from datetime import datetime, timedelta, timezone
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from email.utils import formatdate, getaddresses, make_msgid
import hashlib
import json
import re
import sqlite3
import time
from urllib.parse import quote, urlencode
import uuid

from .common import Stop, digest
from . import google5697 as gg

CLE = 'gmail5697:fournisseur'      # option du cabinet (administrateur), désactivée par défaut
API = 'https://gmail.googleapis.com/gmail/v1/users/me'
BOITE = 'Gmail'
KIND = 'gmail5697'
IMPORT_JOURS, IMPORT_MAX = 7, 250  # import initial borné
HISTOIRE_MAX = 500                 # messages ajoutés lus par passage incrémental
INTERVALLE_MINUTES = 5             # relève périodique
ATTENTE_MAX = 3600                 # secondes : reprise espacée au plus d'une heure
RETENTION_JOURS = 90               # projection locale : messages plus anciens purgés (sauf fil suivi ou projet)
TEXTE_MAX, FIL_MAX, PIECE_MAX = 6000, 12, 25_000_000
EXCLUS = {'SPAM': 'indesirable', 'TRASH': 'corbeille', 'DRAFT': 'brouillon'}
CATEGORIES = {'CATEGORY_PROMOTIONS': 'promotion', 'CATEGORY_SOCIAL': 'reseau_social', 'CATEGORY_UPDATES': 'notification', 'CATEGORY_FORUMS': 'liste_diffusion'}
IDEMPOTENCE = re.compile(r'^[A-Za-z0-9-]{8,64}$')
ADRESSE = re.compile(r'^[^@\s,;<>"]{1,64}@[A-Za-z0-9.-]{1,190}\.[A-Za-z]{2,24}$')
ETATS = {'local': 'Projet local (rien dans Gmail)', 'depot_en_cours': 'Dépôt en cours', 'depose': 'Brouillon déposé dans Gmail (non envoyé)',
         'depot_a_verifier': 'Dépôt à vérifier', 'modifie_a_distance': 'Brouillon modifié dans Gmail : à comparer', 'envoi_en_cours': 'Envoi en cours',
         'envoye': 'Envoyé', 'envoi_a_verifier': 'Envoi à vérifier', 'abandonne': 'Abandonné'}

SCHEMA = '''
CREATE TABLE IF NOT EXISTS gmail5697_messages(connexion_id TEXT NOT NULL, gmail_id TEXT NOT NULL, thread_id TEXT NOT NULL, history_id TEXT NOT NULL DEFAULT '',
  labels TEXT NOT NULL DEFAULT '', de TEXT NOT NULL DEFAULT '', de_adresse TEXT NOT NULL DEFAULT '', a TEXT NOT NULL DEFAULT '', cc TEXT NOT NULL DEFAULT '',
  repondre_a TEXT NOT NULL DEFAULT '', objet TEXT NOT NULL DEFAULT '', recu TEXT NOT NULL DEFAULT '', message_id TEXT NOT NULL DEFAULT '', refs TEXT NOT NULL DEFAULT '',
  texte TEXT NOT NULL DEFAULT '', tronque INTEGER NOT NULL DEFAULT 0, sens TEXT NOT NULL DEFAULT '', motif TEXT NOT NULL DEFAULT '', pieces_json TEXT NOT NULL DEFAULT '[]',
  supprime INTEGER NOT NULL DEFAULT 0, maj TEXT NOT NULL, PRIMARY KEY(connexion_id, gmail_id));
CREATE INDEX IF NOT EXISTS gmail5697_messages_fil ON gmail5697_messages(connexion_id, thread_id);
CREATE TABLE IF NOT EXISTS gmail5697_synchro(connexion_id TEXT PRIMARY KEY, generation INTEGER NOT NULL DEFAULT 0, history_id TEXT NOT NULL DEFAULT '',
  etat TEXT NOT NULL DEFAULT 'a_faire', couverture TEXT NOT NULL DEFAULT '', fenetre_incomplete TEXT NOT NULL DEFAULT '', bail REAL NOT NULL DEFAULT 0,
  prochain_essai REAL NOT NULL DEFAULT 0, tentatives INTEGER NOT NULL DEFAULT 0, erreur TEXT NOT NULL DEFAULT '', derniere TEXT NOT NULL DEFAULT '', maj TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS gmail5697_projets(id TEXT PRIMARY KEY, connexion_id TEXT NOT NULL, owner TEXT NOT NULL, generation INTEGER NOT NULL, matter TEXT NOT NULL DEFAULT '',
  source_id TEXT NOT NULL, thread_id TEXT NOT NULL, a TEXT NOT NULL DEFAULT '', cc TEXT NOT NULL DEFAULT '', objet TEXT NOT NULL DEFAULT '', texte TEXT NOT NULL DEFAULT '',
  pieces_json TEXT NOT NULL DEFAULT '[]', in_reply_to TEXT NOT NULL DEFAULT '', refs TEXT NOT NULL DEFAULT '', message_id TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1,
  etat TEXT NOT NULL DEFAULT 'local', empreinte_depot TEXT NOT NULL DEFAULT '', draft_id TEXT NOT NULL DEFAULT '', draft_message_id TEXT NOT NULL DEFAULT '',
  envoi_cle TEXT NOT NULL DEFAULT '', envoi_par TEXT NOT NULL DEFAULT '', envoi_at TEXT NOT NULL DEFAULT '', envoye_id TEXT NOT NULL DEFAULT '',
  erreur TEXT NOT NULL DEFAULT '', origine TEXT NOT NULL DEFAULT 'avocat', cree TEXT NOT NULL, maj TEXT NOT NULL, UNIQUE(connexion_id, source_id));
CREATE TABLE IF NOT EXISTS gmail5697_journal(id TEXT PRIMARY KEY, projet_id TEXT NOT NULL, action TEXT NOT NULL, idempotence TEXT NOT NULL UNIQUE,
  resultat TEXT NOT NULL DEFAULT '', auteur TEXT NOT NULL, at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS gmail5697_journal_projet ON gmail5697_journal(projet_id, at);
'''


COLONNES = (('ia_generation', 'INTEGER NOT NULL DEFAULT 0'), ('ia_job', 'INTEGER'), ('ia_etat', "TEXT NOT NULL DEFAULT ''"), ('ia_limites', "TEXT NOT NULL DEFAULT '[]'"),
            ('texte_ia', "TEXT NOT NULL DEFAULT ''"), ('corrige', 'INTEGER NOT NULL DEFAULT 0'), ('perime', 'INTEGER NOT NULL DEFAULT 0'))   # préparation par l'IA


def ensure_schema(desk):
    gg.ensure_schema(desk)
    desk.db.executescript(SCHEMA)
    presentes = {r[1] for r in desk.db.execute('PRAGMA table_info(gmail5697_projets)')}
    for nom, decl in COLONNES:                          # migration additive et idempotente
        if nom not in presentes:
            desk.db.execute('ALTER TABLE gmail5697_projets ADD COLUMN %s %s' % (nom, decl))
    if desk.db.in_transaction:
        desk.db.commit()


def _iso():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


# ------------------------------------------------------------------------------------------------ option (C07)
def actif(desk):
    try:
        return desk.settings(CLE, False) is True
    except sqlite3.Error:
        return False


def regler(desk, role, valeur):
    if role != 'administrateur':
        raise Stop('role_insuffisant')
    desk.setting(CLE, valeur is True)
    desk.audit('gmail5697_option', {'actif': valeur is True})
    return {'actif': actif(desk), 'message': 'Fournisseur Gmail %s. Aucune connexion, aucun projet ni aucun suivi n’a été effacé.' % ('activé' if actif(desk) else 'désactivé')}


def connexion_de(desk, owner):
    ensure_schema(desk)
    return gg.connexion(desk, owner, 'gmail')


def _connexion_active(desk, owner, capacite='gmail.lecture'):
    if not actif(desk):
        raise Stop('gmail_desactive')
    row = connexion_de(desk, owner)
    if not row or row['etat'] == 'deconnectee' or row['etat'] == 'en_attente':
        raise Stop('gmail_non_connecte')
    if row['etat'] == 'a_reconnecter':
        raise Stop('google_reconnexion_requise')
    if capacite and capacite not in gg.capacites(row):
        raise Stop('consentement_brouillons_requis' if capacite == 'gmail.brouillons' else 'google_capacite_non_accordee')
    return row


# ------------------------------------------------------------------------------------------------ lecture d'un message Gmail (format « full »)
def _b64(data):
    data = str(data or '')
    return base64.urlsafe_b64decode(data + '=' * (-len(data) % 4))


def _texte_html(html):
    from .mailbox import PlainHTML
    from .bouclier5659 import hidden_marker
    p = PlainHTML()
    p.feed(html)
    return ''.join(p.parts) + hidden_marker(''.join(p.masked_parts))


def _parties(part, out, chemin='0'):
    """Parcours de l'arbre MIME rendu par Gmail : textes (corps inline) et pièces (lues à la demande, par ``attachmentId``)."""
    mime = str(part.get('mimeType') or '')
    corps = part.get('body') or {}
    nom = str(part.get('filename') or '')
    if nom and corps.get('attachmentId'):
        out['pieces'].append({'index': len(out['pieces']), 'name': nom[:200], 'type': mime[:100], 'size': int(corps.get('size') or 0),
                              'attachment_id': str(corps['attachmentId'])})
    elif mime in ('text/plain', 'text/html') and corps.get('data') and not nom:
        try:
            texte = _b64(corps['data']).decode('utf-8', 'replace')
        except (ValueError, TypeError):
            texte = ''
        out.setdefault(mime, texte)
    for n, enfant in enumerate(part.get('parts') or []):
        _parties(enfant, out, '%s.%d' % (chemin, n))


def _adresses(valeur):
    return [a.lower() for _, a in getaddresses([str(valeur or '')]) if '@' in str(a or '')]


def _propres(desk, row):
    from .mailbox import own_addresses
    return {a.lower() for a in own_addresses(desk.c.get('mail') or {})} | {str(row['adresse']).lower()}


def _qualifier(entetes, labels, de_adresse, a, cc, propres):
    """(sens, motif) d'après les SEULS en-têtes et étiquettes : ``recu`` sans motif est le seul cas qui appelle une réponse."""
    if 'SENT' in labels or de_adresse in propres:
        destinataires = _adresses(a) + _adresses(cc)
        return ('copie_soi' if destinataires and all(d in propres for d in destinataires) else 'envoye'), 'message_du_cabinet'
    if not de_adresse or len(_adresses(entetes.get('from', ''))) != 1:
        return 'ambigu', 'expediteur_ambigu'
    for etiquette, motif in EXCLUS.items():
        if etiquette in labels:
            return 'recu', motif
    for etiquette, motif in CATEGORIES.items():
        if etiquette in labels:
            return 'recu', motif
    if entetes.get('list-id') or entetes.get('list-unsubscribe'):
        return 'recu', 'liste_diffusion'
    if entetes.get('auto-submitted', 'no').strip().lower() not in ('', 'no'):
        return 'recu', 'message_automatique'
    if entetes.get('precedence', '').strip().lower() in ('bulk', 'junk', 'list'):
        return 'recu', 'envoi_en_masse'
    if re.match(r'^(no-?reply|ne-?pas-?repondre|mailer-daemon|postmaster)@', de_adresse):
        return 'recu', 'adresse_automatique'
    return 'recu', ''


def _normaliser(desk, row, msg):
    """Message Gmail (format ``full``) -> ligne de la projection : en-têtes RFC, sens, date de réception du serveur, corps borné, pièces."""
    payload = msg.get('payload') or {}
    entetes = {}
    for h in payload.get('headers') or []:
        entetes.setdefault(str(h.get('name') or '').lower(), str(h.get('value') or ''))
    contenu = {'pieces': []}
    _parties(payload, contenu)
    texte = contenu.get('text/plain') or (_texte_html(contenu['text/html']) if contenu.get('text/html') else '')
    texte = texte.replace('\r\n', '\n').strip()
    labels = [str(x) for x in msg.get('labelIds') or []]
    de = _adresses(entetes.get('from', ''))
    de_adresse = de[0] if len(de) == 1 else ''
    sens, motif = _qualifier(entetes, set(labels), de_adresse, entetes.get('to', ''), entetes.get('cc', ''), _propres(desk, row))
    try:
        recu = datetime.fromtimestamp(int(msg.get('internalDate') or 0) / 1000, timezone.utc).isoformat(timespec='seconds')
    except (TypeError, ValueError, OverflowError, OSError):
        recu = ''
    return {'connexion_id': row['id'], 'gmail_id': str(msg['id']), 'thread_id': str(msg.get('threadId') or msg['id']), 'history_id': str(msg.get('historyId') or ''),
            'labels': ' '.join(labels), 'de': entetes.get('from', '')[:300], 'de_adresse': de_adresse, 'a': entetes.get('to', '')[:1000],
            'cc': entetes.get('cc', '')[:1000], 'repondre_a': entetes.get('reply-to', '')[:300], 'objet': entetes.get('subject', '')[:500], 'recu': recu,
            'message_id': entetes.get('message-id', '').strip()[:300], 'refs': entetes.get('references', '')[:4000], 'texte': texte[:TEXTE_MAX],
            'tronque': 1 if len(texte) > TEXTE_MAX else 0, 'sens': sens, 'motif': motif, 'pieces_json': json.dumps(contenu['pieces'], ensure_ascii=False),
            'supprime': 0, 'maj': _iso()}


def _enregistrer(desk, ligne):
    """Écriture idempotente (même message relu = même ligne) ; validée aussitôt : aucun verrou SQLite n'est tenu pendant l'appel suivant."""
    cles = list(ligne)
    desk.db.execute('INSERT INTO gmail5697_messages(%s) VALUES(%s) ON CONFLICT(connexion_id, gmail_id) DO UPDATE SET %s' % (
        ','.join(cles), ','.join('?' * len(cles)), ','.join('%s=excluded.%s' % (k, k) for k in cles if k not in ('connexion_id', 'gmail_id'))), [ligne[k] for k in cles])
    desk.db.commit()


# ------------------------------------------------------------------------------------------------ fournisseur de boîte (contrat C03)
class FournisseurGmail:
    """Contrat de lecture : ``lister``, ``lire_message``, ``lire_fil``, ``lire_piece``. Aucune méthode d'écriture : le dépôt et l'envoi sont des
    fonctions distinctes du module, jamais données à l'analyse. Accès vérifié avant chaque appel (option, connexion de la personne, portée de
    lecture, dossier réservé) ; un message d'une autre connexion n'est jamais lu, même si sa clé est connue."""
    nom = 'gmail'
    CAPACITES = ('lister', 'lire_message', 'lire_fil', 'lire_piece')

    def __init__(self, desk, owner, hidden=frozenset()):
        self.desk, self.owner, self.hidden = desk, str(owner), frozenset(hidden or ())

    def _acces(self, dossiers=()):
        """Garde avant chaque lecture : tous les dossiers que le SERVEUR rattache au message doivent être visibles (B01, ``messages5697``)."""
        if set(dossiers or ()) & self.hidden:
            raise Stop('dossier_reserve')
        return _connexion_active(self.desk, self.owner)

    def _garde(self, gmail_id):
        row = self._acces()
        return self._acces(dossiers_du_message(self.desk, row['id'], gmail_id))

    def fermer(self):
        return None

    def reference(self, mail_key):
        """``gmail:<connexion>:<identifiant Gmail>`` -> référence ; une clé d'une autre connexion est inconnue ici."""
        row = self._acces()
        parties = str(mail_key or '').split(':')
        if len(parties) != 3 or parties[0] != 'gmail' or parties[1] != row['id']:
            raise Stop('message_absent')
        m = self.desk.db.execute('SELECT gmail_id, thread_id FROM gmail5697_messages WHERE connexion_id=? AND gmail_id=?', (row['id'], parties[2])).fetchone()
        if not m:
            raise Stop('message_absent')
        dossiers = dossiers_du_message(self.desk, row['id'], m['gmail_id'])
        self._acces(dossiers)
        return {'compte': 'gmail:' + row['id'], 'dossier': BOITE, 'gmail_id': m['gmail_id'], 'thread_id': m['thread_id'], 'mail_key': str(mail_key),
                'dossiers': sorted(dossiers), 'matter': ''}

    @staticmethod
    def _vue(m):
        return {'uid': m['gmail_id'], 'dossier': BOITE, 'de': m['de'], 'a': m['a'], 'cc': m['cc'], 'objet': m['objet'], 'date': m['recu'],
                'sens': 'envoye' if m['sens'] in ('envoye', 'copie_soi') else 'recu', 'texte': m['texte'], 'tronque': bool(m['tronque']),
                'pieces': [{k: p[k] for k in ('index', 'name', 'type', 'size')} for p in json.loads(m['pieces_json'] or '[]')]}

    def lister(self, boite='', curseur=0):
        row = self._acces()
        debut = max(0, int(curseur or 0))
        rows = self.desk.db.execute('SELECT * FROM gmail5697_messages WHERE connexion_id=? AND supprime=0 ORDER BY recu DESC LIMIT 51 OFFSET ?', (row['id'], debut)).fetchall()
        return {'messages': [dict(self._vue(m), cle='gmail:%s:%s' % (row['id'], m['gmail_id'])) for m in rows[:50]], 'suivant': debut + 50 if len(rows) > 50 else None}

    def lire_message(self, ref):
        row = self._garde(ref['gmail_id'])
        m = self.desk.db.execute('SELECT * FROM gmail5697_messages WHERE connexion_id=? AND gmail_id=? AND supprime=0', (row['id'], ref['gmail_id'])).fetchone()
        if not m:
            raise Stop('message_absent')
        return self._vue(m)

    def lire_fil(self, ref):
        """Messages du fil (reçus et envoyés), du plus ancien au plus récent, texte brut borné : jamais de HTML exécuté ni d'image distante."""
        row = self._garde(ref['gmail_id'])
        from .messages5697 import cle_gmail, dossiers_par_cle
        rows = self.desk.db.execute('SELECT * FROM gmail5697_messages WHERE connexion_id=? AND thread_id=? AND supprime=0 ORDER BY recu', (row['id'], ref['thread_id'])).fetchall()
        assoc = dossiers_par_cle(self.desk, [cle_gmail(row['id'], m['gmail_id']) for m in rows], rapports=False)
        rows = [m for m in rows if not (assoc.get(cle_gmail(row['id'], m['gmail_id']), frozenset()) & self.hidden)]   # chaque message du fil contrôlé (B01)
        return {'messages': [self._vue(m) for m in rows[-FIL_MAX:]], 'limite': '' if len(rows) <= FIL_MAX else 'fil_borne_%d' % FIL_MAX}

    def lire_piece(self, ref, index):
        """Pièce lue à la demande chez Google (``messages.attachments.get``), texte extrait localement et borné."""
        row = self._garde(ref['gmail_id'])
        m = self.desk.db.execute('SELECT pieces_json FROM gmail5697_messages WHERE connexion_id=? AND gmail_id=?', (row['id'], ref['gmail_id'])).fetchone()
        try:
            piece = json.loads(m['pieces_json'] or '[]')[int(index)] if m else None
        except (ValueError, IndexError, TypeError):
            piece = None
        if not piece:
            raise Stop('piece_jointe_absente')
        out = gg.appeler(self.desk, row['id'], 'GET', '%s/messages/%s/attachments/%s' % (API, quote(ref['gmail_id'], safe=''), quote(piece['attachment_id'], safe='')),
                         capacite='gmail.lecture')
        brut = _b64(out.get('data'))
        if len(brut) > PIECE_MAX:
            raise Stop('piece_trop_volumineuse')
        from .documents import extract
        texte = extract(brut, piece['name'], {**self.desk.c.get('documents', {}), 'max_document_chars': 200000})
        return {'name': piece['name'], 'text': texte[:60000], 'truncated': len(texte) > 60000}


# ------------------------------------------------------------------------------------------------ synchronisation (hors requête de page)
def _synchro(desk, conn_id):
    desk.db.execute('INSERT OR IGNORE INTO gmail5697_synchro(connexion_id, maj) VALUES(?,?)', (conn_id, _iso()))
    desk.db.commit()
    return dict(desk.db.execute('SELECT * FROM gmail5697_synchro WHERE connexion_id=?', (conn_id,)).fetchone())


def _maj_synchro(desk, conn_id, **valeurs):
    desk.db.execute('INSERT OR IGNORE INTO gmail5697_synchro(connexion_id, maj) VALUES(?,?)', (conn_id, _iso()))
    valeurs['maj'] = _iso()
    desk.db.execute('UPDATE gmail5697_synchro SET %s WHERE connexion_id=?' % ','.join('%s=?' % k for k in valeurs), list(valeurs.values()) + [conn_id])
    desk.db.commit()


def _lire_complet(desk, row, gid):
    return gg.appeler(desk, row['id'], 'GET', '%s/messages/%s?format=full' % (API, quote(str(gid), safe='')), capacite='gmail.lecture')


def _import(desk, row, resynchro=False):
    """Import borné : profil (adresse vérifiée, point de reprise relevé AVANT la liste), puis les messages des 7 derniers jours, 250 au plus."""
    profil = gg.appeler(desk, row['id'], 'GET', API + '/profile', capacite='gmail.lecture')
    if str(profil.get('emailAddress') or '').lower() != row['adresse']:
        raise Stop('google_compte_different')
    reprise = str(profil.get('historyId') or '')
    ids, page = [], None
    while len(ids) < IMPORT_MAX:
        params = {'q': 'newer_than:%dd' % IMPORT_JOURS, 'maxResults': min(100, IMPORT_MAX - len(ids))}
        if page:
            params['pageToken'] = page
        out = gg.appeler(desk, row['id'], 'GET', API + '/messages?' + urlencode(params), capacite='gmail.lecture')
        ids += [str(m['id']) for m in out.get('messages') or [] if m.get('id')]
        page = out.get('nextPageToken')
        if not page:
            break
    for gid in dict.fromkeys(ids):
        try:
            _enregistrer(desk, _normaliser(desk, row, _lire_complet(desk, row, gid)))
        except gg.ErreurGoogle as error:
            if error.statut != 404:          # supprimé entre la liste et la lecture : ignoré ; le reste remonte
                raise
    fenetre = ('Historique Gmail expiré : seuls les %d derniers jours ont été relus ; des changements plus anciens peuvent manquer.' % IMPORT_JOURS) if resynchro else ''
    _maj_synchro(desk, row['id'], history_id=reprise, generation=row['generation'], couverture='partielle' if page else 'complete',
                 fenetre_incomplete=fenetre, etat='a_jour', erreur='', tentatives=0, prochain_essai=0, derniere=_iso())
    return {'importes': len(ids), 'couverture': 'partielle' if page else 'complete', 'resynchronisation': resynchro}


def _incremental(desk, row, depuis):
    """``history.list`` paginé : ajouts, suppressions et changements d'étiquettes, appliqués localement ; curseur avancé après la série."""
    ajouts, suppressions, etiquettes, page, dernier = {}, set(), {}, None, depuis
    while True:
        params = {'startHistoryId': depuis, 'maxResults': 500}
        if page:
            params['pageToken'] = page
        out = gg.appeler(desk, row['id'], 'GET', API + '/history?' + urlencode(params), capacite='gmail.lecture')
        for h in out.get('history') or []:
            for x in h.get('messagesAdded') or []:
                m = x.get('message') or {}
                if m.get('id'):
                    ajouts[str(m['id'])] = True
                    suppressions.discard(str(m['id']))
            for x in h.get('messagesDeleted') or []:
                m = x.get('message') or {}
                if m.get('id'):
                    suppressions.add(str(m['id']))
                    ajouts.pop(str(m['id']), None)
            for cle, signe in (('labelsAdded', 1), ('labelsRemoved', -1)):
                for x in h.get(cle) or []:
                    m = x.get('message') or {}
                    if m.get('id'):
                        etiquettes.setdefault(str(m['id']), []).append((signe, [str(l) for l in x.get('labelIds') or []]))
        dernier = str(out.get('historyId') or dernier)
        page = out.get('nextPageToken')
        if not page:
            break
    partielle = len(ajouts) > HISTOIRE_MAX
    for gid in list(ajouts)[:HISTOIRE_MAX]:
        try:
            _enregistrer(desk, _normaliser(desk, row, _lire_complet(desk, row, gid)))
        except gg.ErreurGoogle as error:
            if error.statut != 404:
                raise
    for gid, changements in etiquettes.items():
        if gid in ajouts:
            continue                          # déjà relu en entier
        m = desk.db.execute('SELECT labels FROM gmail5697_messages WHERE connexion_id=? AND gmail_id=?', (row['id'], gid)).fetchone()
        if not m:
            continue
        actuelles = set(m['labels'].split())
        for signe, liste in changements:
            actuelles = (actuelles | set(liste)) if signe > 0 else (actuelles - set(liste))
        desk.db.execute('UPDATE gmail5697_messages SET labels=?, supprime=CASE WHEN ? THEN 1 ELSE supprime END, maj=? WHERE connexion_id=? AND gmail_id=?',
                        (' '.join(sorted(actuelles)), 1 if 'TRASH' in actuelles else 0, _iso(), row['id'], gid))
    for gid in suppressions:
        desk.db.execute('UPDATE gmail5697_messages SET supprime=1, maj=? WHERE connexion_id=? AND gmail_id=?', (_iso(), row['id'], gid))
    desk.db.commit()                          # série écrite durablement : le curseur peut avancer
    _maj_synchro(desk, row['id'], history_id=dernier, etat='a_jour', erreur='', tentatives=0, prochain_essai=0, derniere=_iso(),
                 **({'couverture': 'partielle', 'fenetre_incomplete': 'Plus de %d messages nouveaux d’un coup : les plus anciens de la série n’ont pas été relus.' % HISTOIRE_MAX}
                    if partielle else {}))
    return {'ajoutes': min(len(ajouts), HISTOIRE_MAX), 'supprimes': len(suppressions), 'etiquettes': len(etiquettes), 'couverture': 'partielle' if partielle else ''}


def _retention(desk, conn_id):
    """Cache borné : messages de plus de 90 jours purgés, sauf ceux d'un fil suivi ou d'un projet."""
    limite = (datetime.now(timezone.utc) - timedelta(days=RETENTION_JOURS)).isoformat()
    desk.db.execute('DELETE FROM gmail5697_messages WHERE connexion_id=? AND recu<? AND thread_id NOT IN (SELECT thread_id FROM gmail5697_projets WHERE connexion_id=?)',
                    (conn_id, limite, conn_id))
    desk.db.commit()


def synchroniser(desk, conn_id):
    """Un passage de synchronisation pour une connexion : un seul actif par boîte, reprise espacée, arrêt sur autorisation retirée."""
    ensure_schema(desk)
    row = gg.lire(desk, conn_id)
    if not actif(desk):
        return {'etat': 'desactive'}
    s = _synchro(desk, row['id'])
    if row['etat'] != 'active':
        _maj_synchro(desk, row['id'], etat='arrete', erreur='google_reconnexion_requise' if row['etat'] == 'a_reconnecter' else 'gmail_non_connecte')
        return {'etat': 'arrete'}
    t = gg.HORLOGE()
    if s['prochain_essai'] > t:
        return {'etat': 'espace', 'dans': int(s['prochain_essai'] - t)}
    cur = desk.db.execute('UPDATE gmail5697_synchro SET bail=? WHERE connexion_id=? AND bail<?', (t + 600, row['id'], t))
    desk.db.commit()
    if not cur.rowcount:
        return {'etat': 'deja_en_cours'}
    try:
        if s['generation'] != row['generation']:                 # nouvelle autorisation : on repart d'un import borné
            _maj_synchro(desk, row['id'], history_id='', generation=row['generation'])
            s['history_id'] = ''
        try:
            out = _incremental(desk, row, s['history_id']) if s['history_id'] else _import(desk, row)
        except gg.ErreurGoogle as error:
            if error.statut != 404 or not s['history_id']:
                raise
            out = _import(desk, row, resynchro=True)             # curseur trop ancien : resynchronisation bornée, fenêtre signalée
        _retention(desk, row['id'])
        return {'etat': 'a_jour', **out}
    except Stop as error:
        code = str(error).split(':')[0]
        statut = getattr(error, 'statut', 0)
        if code in ('google_reconnexion_requise', 'google_non_connecte', 'google_compte_different', 'google_capacite_non_accordee'):
            _maj_synchro(desk, row['id'], etat='arrete', erreur=code)
            return {'etat': 'arrete', 'erreur': code}
        if statut == 429 or statut >= 500 or code in ('delai_http_depasse', 'connexion_http_indisponible'):
            n = int(s['tentatives']) + 1
            attente = min(ATTENTE_MAX, max(int(getattr(error, 'attente', 0) or 0), 60 * 2 ** min(n - 1, 6)))
            _maj_synchro(desk, row['id'], etat='espace', erreur=code, tentatives=n, prochain_essai=gg.HORLOGE() + attente)
            return {'etat': 'espace', 'erreur': code, 'dans': attente}
        _maj_synchro(desk, row['id'], etat='erreur', erreur=code)
        return {'etat': 'erreur', 'erreur': code}
    finally:
        desk.db.execute('UPDATE gmail5697_synchro SET bail=0 WHERE connexion_id=?', (row['id'],))
        desk.db.commit()


def job(desk, args=None):
    """Travail ``gmail5697`` : une connexion, ou toutes les connexions actives."""
    args = args or {}
    ensure_schema(desk)
    ids = [str(args['connexion'])] if args.get('connexion') else [r[0] for r in desk.db.execute(
        "SELECT id FROM google5697_connexions WHERE usage='gmail' AND etat='active'")]
    return {'synchronisations': {i[:10]: synchroniser(desk, i).get('etat') for i in ids}}


def schedule(desk, now_ts):
    """Boucle des automatismes : relève toutes les 5 minutes si le fournisseur est activé et qu'une connexion est active."""
    if not actif(desk):
        return None
    ensure_schema(desk)
    if not desk.db.execute("SELECT 1 FROM google5697_connexions WHERE usage='gmail' AND etat='active' LIMIT 1").fetchone():
        return None
    from . import cadence5662
    if now_ts - float(desk.settings('auto:gmail5697', 0) or 0) < INTERVALLE_MINUTES * 60 or not cadence5662.allow(desk, KIND, INTERVALLE_MINUTES, now_ts):
        return None
    ident = desk.enqueue(KIND, {}, priority=45)
    desk.setting('auto:gmail5697', now_ts)
    return ident


def demander_synchro(desk, owner):
    """« Synchroniser maintenant » : met la relève de SA boîte en file (aucun appel Google dans la requête de page)."""
    row = _connexion_active(desk, owner)
    return {'job': desk.enqueue(KIND, {'connexion': row['id']}, priority=30), 'message': 'Relève demandée : la liste se mettra à jour d’elle-même. Rien n’est écrit dans Gmail.'}


def deconnecter(desk, owner):
    """Déconnexion : file arrêtée, jeton révoqué, jetons et cache local des messages effacés ; projets et suivis conservés."""
    row = connexion_de(desk, owner)
    if not row:
        raise Stop('gmail_non_connecte')
    out = gg.deconnecter(desk, owner, row['id'])
    desk.db.execute('DELETE FROM gmail5697_messages WHERE connexion_id=?', (row['id'],))
    desk.db.execute("UPDATE gmail5697_synchro SET history_id='', etat='arrete', erreur='gmail_deconnecte', bail=0, maj=? WHERE connexion_id=?", (_iso(), row['id']))
    desk.db.commit()
    return out


def etat_synchro(desk, owner):
    row = connexion_de(desk, owner)
    if not row:
        return None
    s = desk.db.execute('SELECT * FROM gmail5697_synchro WHERE connexion_id=?', (row['id'],)).fetchone()
    return dict(s) if s else None


# ------------------------------------------------------------------------------------------------ lignes de la vue « À traiter avec le Pilote »
def fil_de(conn_id, thread_id):
    return digest('gmail|%s|%s' % (conn_id, thread_id))


def projet_du_fil(desk, conn_id, thread_id):
    row = desk.db.execute("SELECT * FROM gmail5697_projets WHERE connexion_id=? AND thread_id=? AND etat<>'abandonne' ORDER BY maj DESC LIMIT 1",
                          (conn_id, thread_id)).fetchone()
    return dict(row) if row else None


def lignes_vue(desk, owner, hidden, statuts, dossier, boite, suivis, maintenant):
    """Fils Gmail de la personne, lus dans la projection locale (aucun appel Google) ; même forme que les lignes IMAP de ``courriels5696``.
    Rien si le fournisseur est désactivé ou si la personne n'a pas de connexion Gmail : la vue IMAP reste exactement la même."""
    from . import courriels5696 as cx
    if not actif(desk) or (boite and boite != BOITE):
        return []
    ensure_schema(desk)
    row = connexion_de(desk, owner)
    if not row or row['etat'] not in ('active', 'a_reconnecter'):
        return []
    compte = 'gmail:' + row['id']
    depuis = (maintenant - timedelta(days=cx.FENETRE_JOURS)).isoformat()
    groupes = {}
    for m, dossiers in _visibles(desk, row['id'], hidden, cx.LIMITE):
        groupes.setdefault(m['thread_id'], (m, dossiers))  # le plus récent reçu VISIBLE du fil représente la ligne
    from .common import load_matters, matter_display
    labels = {x['id']: matter_display(x) for x in load_matters(desk.c)}
    out = []
    from .messages5697 import principal
    for thread, (m, dossiers) in groupes.items():
        fil = fil_de(row['id'], thread)
        suivi = suivis.get((compte, BOITE, fil))
        if m['recu'] < depuis and not suivi:
            continue
        projet = projet_du_fil(desk, row['id'], thread)
        suivi_matter = (suivi or {}).get('matter') or ''
        if suivi_matter in hidden:                         # le suivi d'un fil ne donne aucun droit (B01)
            continue
        matter = principal(dossiers, (projet or {}).get('matter') or '') or ('' if dossiers else suivi_matter)
        if matter in hidden or (dossier and matter != dossier):
            continue
        cle = 'gmail:%s:%s' % (row['id'], m['gmail_id'])
        nouveau = bool(suivi and suivi['mail_key'] != cle and cx._instant_de(m['recu']) > cx._instant_de(suivi['maj']))
        if projet and projet['etat'] == 'envoye' and not nouveau and (not suivi or suivi['statut'] not in ('ecarte',)):
            statut, precision = 'termine', 'Réponse envoyée depuis Gmail'
        else:
            statut, precision = cx.statut_de('', suivi, projet, nouveau, None, maintenant)
            if projet and statut in ('a_traiter', 'a_completer'):      # projet local préparé par l'avocat (sans travail ni décision « Préparer »)
                statut = 'reponse_prete'
            if projet and projet['etat'] == 'envoi_a_verifier':
                precision = 'Envoi à vérifier'
            elif projet and projet.get('ia_etat') == 'en_cours' and not projet.get('texte'):
                statut, precision = 'en_preparation', IA_ETATS['en_cours']
            elif projet and projet.get('perime'):
                precision = 'Projet périmé : nouveau message'
        if statut not in statuts:
            continue
        out.append({'fil': fil, 'mail_key': cle, 'compte': compte, 'adresse': row['adresse'], 'boite': BOITE, 'de': m['de'], 'objet': m['objet'] or '(sans objet)',
                    'recu': m['recu'], 'matter': matter, 'dossier': labels.get(matter, ''), 'statut': statut, 'libelle': cx.LIBELLES[statut], 'precision': precision,
                    'version': (suivi or {}).get('version', 0), 'tache': '', 'raison': '', 'projet': (projet or {}).get('id', ''),
                    'suivi': {k: (suivi or {}).get(k, '') for k in ('statut', 'motif', 'attente_de', 'echeance', 'fuseau', 'auteur', 'maj')} if suivi else None,
                    'fournisseur': 'gmail'})
    return out


def _visibles(desk, conn_id, hidden, limite):
    """5.6.97 (B01) : messages reçus qui appellent une réponse, contrôlés UN PAR UN sur leurs rattachements du serveur (``messages5697``)
    AVANT la borne, le regroupement par fil et toute lecture : [(ligne, dossiers)], du plus récent au plus ancien."""
    from .messages5697 import cle_gmail, dossiers_par_cle
    gardes, decalage = [], 0
    while len(gardes) < limite:
        lot = desk.db.execute("SELECT * FROM gmail5697_messages WHERE connexion_id=? AND supprime=0 AND sens='recu' AND motif='' ORDER BY recu DESC, gmail_id "
                              'LIMIT ? OFFSET ?', (conn_id, limite, decalage)).fetchall()
        if not lot:
            break
        decalage += len(lot)
        cles = {m['gmail_id']: cle_gmail(conn_id, m['gmail_id']) for m in lot}
        assoc = dossiers_par_cle(desk, list(cles.values()), rapports=False)
        gardes += [(m, assoc.get(cles[m['gmail_id']], frozenset())) for m in lot if not (assoc.get(cles[m['gmail_id']], frozenset()) & hidden)]
    return gardes[:limite]


def rattachements_messages(desk, cles):
    """Pour ``messages5697`` : dossier enregistré sur le serveur pour le projet de réponse d'un message Gmail (choix de l'avocat), par clé canonique."""
    from .messages5697 import cle_gmail
    voulues, out = set(cles), {}
    for conn_id, source, matter in desk.db.execute("SELECT connexion_id, source_id, matter FROM gmail5697_projets WHERE matter<>''"):
        cle = cle_gmail(conn_id, source)
        if cle in voulues:
            out.setdefault(cle, set()).add(str(matter))
    return out


def dossiers_du_message(desk, conn_id, gmail_id):
    """Dossiers que le serveur rattache à ce message Gmail (résolveur canonique ``messages5697``)."""
    from .messages5697 import resoudre
    return resoudre(desk, {'connexion_id': conn_id, 'gmail_id': gmail_id}).dossiers


# ------------------------------------------------------------------------------------------------ projets locaux (commande de l'avocat, sans IA)
def _journal(desk, projet_id, action, idempotence, auteur, resultat=''):
    desk.db.execute('INSERT INTO gmail5697_journal(id, projet_id, action, idempotence, resultat, auteur, at) VALUES(?,?,?,?,?,?,?)',
                    (uuid.uuid4().hex, projet_id, action, idempotence, resultat, auteur, _iso()))


def _deja(desk, idempotence):
    if not IDEMPOTENCE.match(str(idempotence or '')):
        raise Stop('idempotence_requise')
    row = desk.db.execute('SELECT * FROM gmail5697_journal WHERE idempotence=?', (str(idempotence),)).fetchone()
    return dict(row) if row else None


def projet(desk, owner, projet_id):
    ensure_schema(desk)
    row = desk.db.execute('SELECT * FROM gmail5697_projets WHERE id=?', (str(projet_id or ''),)).fetchone()
    if not row or row['owner'] != str(owner):
        raise Stop('projet_absent')                         # le projet d'une autre personne n'existe pas pour celle-ci
    return dict(row)


def _liste_adresses(valeur, limite=20):
    sortie = []
    for brut in re.split(r'[,;\n]+', str(valeur or '')):
        brut = brut.strip()
        if not brut:
            continue
        adresses = _adresses(brut)
        if len(adresses) != 1 or not ADRESSE.match(adresses[0]):
            raise Stop('adresse_invalide')
        sortie.append(adresses[0])
    sortie = list(dict.fromkeys(sortie))
    if len(sortie) > limite:
        raise Stop('trop_de_destinataires')
    return sortie


def preparer(desk, owner, mail_key, matter='', texte='', hidden=frozenset()):
    """« Préparer une réponse » sur un message Gmail : projet LOCAL (rien dans Gmail), un seul par message source ; destinataire = l'expéditeur
    (réponse à tous désactivée par défaut, aucune adresse tirée d'un texte) ; objet et références cohérents avec le fil."""
    row = _connexion_active(desk, owner)
    parties = str(mail_key or '').split(':')
    if len(parties) != 3 or parties[0] != 'gmail' or parties[1] != row['id']:
        raise Stop('message_absent')
    m = desk.db.execute('SELECT * FROM gmail5697_messages WHERE connexion_id=? AND gmail_id=? AND supprime=0', (row['id'], parties[2])).fetchone()
    if not m:
        raise Stop('message_absent')
    if (dossiers_du_message(desk, row['id'], m['gmail_id']) | ({str(matter)} if matter else set())) & frozenset(hidden or ()):
        raise Stop('dossier_reserve')                       # B01 : message ou dossier choisi réservés à d'autres : rien n'est préparé
    if m['sens'] != 'recu' or m['motif']:
        raise Stop('message_sans_reponse')                  # envoyé, copie à soi, notification : jamais une demande de réponse
    existant = desk.db.execute('SELECT id FROM gmail5697_projets WHERE connexion_id=? AND source_id=?', (row['id'], m['gmail_id'])).fetchone()
    if existant:
        return {'projet': existant[0], 'deja': True, 'message': 'Un projet existe déjà pour ce message.'}
    objet = m['objet'] if re.match(r'^\s*re\s*:', m['objet'], re.I) else 'Re: ' + m['objet']
    refs = ' '.join(x for x in (m['refs'].split() + [m['message_id']]) if x)[-3900:]
    domaine = row['adresse'].rsplit('@', 1)[-1]
    ident, maintenant = uuid.uuid4().hex, _iso()
    desk.db.execute('INSERT OR IGNORE INTO gmail5697_projets(id, connexion_id, owner, generation, matter, source_id, thread_id, a, cc, objet, texte, in_reply_to, refs, message_id, cree, maj) '
                    'VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)', (ident, row['id'], str(owner), row['generation'], str(matter or ''), m['gmail_id'], m['thread_id'], m['de_adresse'], '',
                                                              objet[:300], str(texte or '')[:50000], m['message_id'], refs, make_msgid(domain=domaine), maintenant, maintenant))
    desk.db.commit()
    gagnant = desk.db.execute('SELECT id FROM gmail5697_projets WHERE connexion_id=? AND source_id=?', (row['id'], m['gmail_id'])).fetchone()[0]
    desk.audit('gmail5697_projet', {'projet': gagnant[:12]})
    return {'projet': gagnant, 'deja': gagnant != ident, 'message': 'Projet local créé : rien n’a été déposé ni envoyé.'}


def modifier(desk, owner, projet_id, version, data):
    """Correction du projet par l'avocat (destinataires, objet, texte), sous verrou de version. Un projet déjà déposé redevient local :
    le brouillon Gmail sera mis à jour au prochain dépôt, après comparaison."""
    p = projet(desk, owner, projet_id)
    if p['etat'] not in ('local', 'depose', 'modifie_a_distance'):
        raise Stop('etat_incompatible')
    a = _liste_adresses(data.get('a', p['a']))
    if not a:
        raise Stop('destinataire_requis')
    cc = _liste_adresses(data.get('cc', p['cc']))
    objet = re.sub(r'[\r\n]+', ' ', str(data.get('objet', p['objet']) or '')).strip()[:300]
    texte = str(data.get('texte', p['texte']) or '')[:50000]
    cur = desk.db.execute("UPDATE gmail5697_projets SET a=?, cc=?, objet=?, texte=?, etat=CASE WHEN etat='depose' THEN 'local' ELSE etat END, corrige=1, perime=0, "
                          "version=version+1, maj=? WHERE id=? AND version=?", (', '.join(a), ', '.join(cc), objet, texte, _iso(), p['id'], int(version)))   # corrigé par l'avocat
    desk.db.commit()
    if not cur.rowcount:
        raise Stop('version_perimee')
    return vue_projet(desk, owner, p['id'])


def abandonner(desk, owner, projet_id, version):
    p = projet(desk, owner, projet_id)
    if p['etat'] not in ('local', 'modifie_a_distance', 'depose'):
        raise Stop('etat_incompatible')
    cur = desk.db.execute("UPDATE gmail5697_projets SET etat='abandonne', version=version+1, maj=? WHERE id=? AND version=?", (_iso(), p['id'], int(version)))
    desk.db.commit()
    if not cur.rowcount:
        raise Stop('version_perimee')
    return {'message': 'Projet abandonné. Un brouillon déjà déposé reste dans Gmail, où vous pouvez le supprimer.'}


# ------------------------------------------------------------------------------------------------ MIME et empreinte de relecture
def _mime(p, adresse, nom=''):
    msg = EmailMessage(policy=policy.SMTP)
    msg['From'] = '%s <%s>' % (re.sub(r'[<>"\r\n]', '', nom), adresse) if nom else adresse
    msg['To'] = p['a']
    if p['cc']:
        msg['Cc'] = p['cc']
    msg['Subject'] = p['objet']
    msg['Date'] = formatdate(localtime=False)
    msg['Message-ID'] = p['message_id']
    if p['in_reply_to']:
        msg['In-Reply-To'] = p['in_reply_to']
    if p['refs']:
        msg['References'] = p['refs']
    msg.set_content(p['texte'] or '', charset='utf-8')
    return msg.as_bytes()


def releve(raw):
    """Ce qui partira, relu dans le MIME lui-même : compte d'expédition, destinataires, objet, texte, pièces (nom, taille, empreinte)."""
    msg = BytesParser(policy=policy.default).parsebytes(raw)
    corps = msg.get_body(preferencelist=('plain',))
    texte = ''
    if corps is not None:
        try:
            texte = corps.get_content()
        except (LookupError, UnicodeError):
            texte = ''
    pieces = []
    for part in msg.iter_attachments():
        donnees = part.get_payload(decode=True) or b''
        pieces.append({'nom': str(part.get_filename() or ''), 'taille': len(donnees), 'empreinte': hashlib.sha256(donnees).hexdigest()[:16]})
    return {'compte': (_adresses(msg.get('From', '')) or [''])[0], 'a': sorted(_adresses(msg.get('To', ''))), 'cc': sorted(_adresses(msg.get('Cc', ''))),
            'objet': str(msg.get('Subject', '')), 'texte': str(texte).replace('\r\n', '\n').rstrip('\n'), 'pieces': pieces,
            'message_id': str(msg.get('Message-ID', '')).strip(), 'in_reply_to': str(msg.get('In-Reply-To', '')).strip()}


def empreinte(r):
    return hashlib.sha256(json.dumps({k: r[k] for k in ('compte', 'a', 'cc', 'objet', 'texte', 'pieces')}, sort_keys=True, ensure_ascii=False).encode('utf-8')).hexdigest()


def _distant(desk, conn_id, draft_id):
    """Brouillon distant relu (format brut) ; ``None`` s'il n'existe plus."""
    try:
        out = gg.appeler(desk, conn_id, 'GET', '%s/drafts/%s?format=raw' % (API, quote(draft_id, safe='')), capacite='gmail.brouillons')
    except gg.ErreurGoogle as error:
        if error.statut == 404:
            return None
        raise
    message = out.get('message') or {}
    return {'id': str(out.get('id') or draft_id), 'message_id': str(message.get('id') or ''), 'releve': releve(_b64(message.get('raw')))}


def vue_projet(desk, owner, projet_id):
    """Projet et relecture exacte de ce qui partirait (relue dans le MIME composé, jamais recomposée à part)."""
    p = projet(desk, owner, projet_id)
    row = gg.lire(desk, p['connexion_id'])
    r = releve(_mime(p, row['adresse']))
    return {'id': p['id'], 'etat': p['etat'], 'libelle': ETATS.get(p['etat'], p['etat']), 'version': p['version'], 'a': p['a'], 'cc': p['cc'], 'objet': p['objet'],
            'texte': p['texte'], 'matter': p['matter'], 'releve': r, 'empreinte': empreinte(r), 'empreinte_depot': p['empreinte_depot'], 'erreur': p['erreur'],
            'compte': row['adresse'], 'draft': bool(p['draft_id']), 'envoye_id': p['envoye_id'], 'ia_etat': p['ia_etat'], 'ia_libelle': IA_ETATS.get(p['ia_etat'], ''),
            'ia_limites': json.loads(p['ia_limites'] or '[]'), 'texte_ia': p['texte_ia'], 'perime': bool(p['perime']), 'corrige': bool(p['corrige'])}


def _exiger_droit(desk, owner, role, droit):
    from .membres5650 import effective_rights
    try:
        if effective_rights(desk.c['state_dir'], owner, role).get(droit):
            return
    except Stop:
        pass
    raise Stop('role_insuffisant')


def _exiger_personne(desk):
    """Garde-fou : l'envoi et le dépôt sont des gestes d'une personne, jamais d'un travail de l'agent ni d'un automatisme."""
    if getattr(desk, 'active_job_id', None) or getattr(desk, 'origin_context', None) == 'automatic':
        raise Stop('geste_reserve_a_la_personne')


def _restaurer(desk, p_id, etat, erreur):
    desk.db.execute('UPDATE gmail5697_projets SET etat=?, erreur=?, maj=? WHERE id=?', (etat, erreur, _iso(), p_id))
    desk.db.commit()


def deposer(desk, owner, role, projet_id, version, idempotence):
    """Dépôt (ou mise à jour) du brouillon dans le fil Gmail, relu après dépôt. Jamais d'envoi ici."""
    ensure_schema(desk)
    deja = _deja(desk, idempotence)
    if deja:
        return {'deja': True, 'resultat': deja['resultat'], **vue_projet(desk, owner, deja['projet_id'])}
    _exiger_personne(desk)
    _exiger_droit(desk, owner, role, 'deposer')
    p = projet(desk, owner, projet_id)
    row = _connexion_active(desk, owner, 'gmail.brouillons')
    if row['id'] != p['connexion_id'] or row['generation'] != p['generation']:
        raise Stop('compte_modifie')
    if p['etat'] == 'depot_a_verifier':
        raise Stop('depot_a_verifier')
    if p['etat'] != 'local':
        raise Stop('etat_incompatible')
    if p['perime']:
        raise Stop('projet_perime')                         # nouveau message arrivé pendant la préparation : relire et corriger d'abord
    if int(version) != p['version']:
        raise Stop('version_perimee')
    if not p['a']:
        raise Stop('destinataire_requis')
    if p['draft_id']:                                       # mise à jour : le brouillon distant ne doit pas avoir changé depuis le dépôt
        distant = _distant(desk, row['id'], p['draft_id'])
        if distant is not None and empreinte(distant['releve']) != p['empreinte_depot']:
            _restaurer(desk, p['id'], 'modifie_a_distance', 'brouillon_modifie_a_distance')
            raise Stop('brouillon_modifie_a_distance')
        if distant is None:
            p['draft_id'] = ''
    raw = _mime(p, row['adresse'])
    attendu = empreinte(releve(raw))
    desk.db.execute('BEGIN IMMEDIATE')
    try:
        cur = desk.db.execute("UPDATE gmail5697_projets SET etat='depot_en_cours', erreur='', version=version+1, maj=? WHERE id=? AND version=? AND etat='local'",
                              (_iso(), p['id'], p['version']))
        if not cur.rowcount:
            raise Stop('version_perimee')
        _journal(desk, p['id'], 'deposer', idempotence, owner)
        desk.db.commit()
    except BaseException:
        if desk.db.in_transaction:
            desk.db.rollback()
        raise
    corps = {'message': {'raw': base64.urlsafe_b64encode(raw).decode().rstrip('='), 'threadId': p['thread_id']}}
    try:
        if p['draft_id']:
            out = gg.appeler(desk, row['id'], 'PUT', '%s/drafts/%s' % (API, quote(p['draft_id'], safe='')), {**corps, 'id': p['draft_id']}, capacite='gmail.brouillons')
        else:
            out = gg.appeler(desk, row['id'], 'POST', API + '/drafts', corps, capacite='gmail.brouillons')
    except Stop as error:
        statut, code = getattr(error, 'statut', 0), str(error).split(':')[0]
        if (400 <= statut < 500 and statut != 408) or code in ('google_reconnexion_requise', 'google_non_connecte', 'google_capacite_non_accordee'):
            _restaurer(desk, p['id'], 'local', code)          # refus certain : rien n'a été déposé, l'avocat peut réessayer
            raise
        _restaurer(desk, p['id'], 'depot_a_verifier', code)  # réponse perdue : on vérifiera avant tout nouveau dépôt (zéro doublon)
        return {'deja': False, 'resultat': 'a_verifier', **vue_projet(desk, owner, p['id'])}
    draft_id = str(out.get('id') or '')
    distant = _distant(desk, row['id'], draft_id) if draft_id else None
    if distant is None or empreinte(distant['releve']) != attendu:
        desk.db.execute("UPDATE gmail5697_projets SET etat='depot_a_verifier', draft_id=?, erreur='relecture_differente', maj=? WHERE id=?", (draft_id, _iso(), p['id']))
        desk.db.commit()
        return {'deja': False, 'resultat': 'a_verifier', **vue_projet(desk, owner, p['id'])}
    desk.db.execute("UPDATE gmail5697_projets SET etat='depose', draft_id=?, draft_message_id=?, empreinte_depot=?, erreur='', maj=? WHERE id=?",
                    (draft_id, distant['message_id'], attendu, _iso(), p['id']))
    desk.db.execute("UPDATE gmail5697_journal SET resultat='depose' WHERE idempotence=?", (idempotence,))
    desk.db.commit()
    desk.audit('gmail5697_depot', {'projet': p['id'][:12]})
    return {'deja': False, 'resultat': 'depose', **vue_projet(desk, owner, p['id'])}


def envoyer(desk, owner, role, projet_id, version, empreinte_relue, idempotence, relu=False):
    """« Envoyer maintenant » : la version relue (empreinte), une seule fois. Rien n'est renvoyé automatiquement."""
    ensure_schema(desk)
    deja = _deja(desk, idempotence)
    if deja:                                                # double clic, nouvel essai réseau : le premier geste, rien de plus
        return {'deja': True, 'resultat': deja['resultat'], **vue_projet(desk, owner, deja['projet_id'])}
    _exiger_personne(desk)
    if relu is not True:
        raise Stop('relecture_requise')
    _exiger_droit(desk, owner, role, 'envoyer')
    p = projet(desk, owner, projet_id)
    row = _connexion_active(desk, owner, 'gmail.brouillons')
    if row['id'] != p['connexion_id'] or row['generation'] != p['generation']:
        raise Stop('compte_modifie')
    if p['etat'] in ('envoye', 'envoi_en_cours', 'envoi_a_verifier'):
        raise Stop('envoi_deja_demande')
    if p['etat'] != 'depose':
        raise Stop('brouillon_a_deposer')
    if int(version) != p['version'] or str(empreinte_relue or '') != p['empreinte_depot']:
        raise Stop('version_perimee')                       # projet modifié après la relecture : relire de nouveau
    distant = _distant(desk, row['id'], p['draft_id'])
    if distant is None:
        _restaurer(desk, p['id'], 'local', 'brouillon_distant_absent')
        raise Stop('brouillon_distant_absent')
    if empreinte(distant['releve']) != p['empreinte_depot']:
        _restaurer(desk, p['id'], 'modifie_a_distance', 'brouillon_modifie_a_distance')
        raise Stop('brouillon_modifie_a_distance')
    acces = gg.jeton(desk, row['id'])                       # renouvelé AVANT la réservation : une panne ici n'engage rien
    desk.db.execute('BEGIN IMMEDIATE')
    try:
        cur = desk.db.execute("UPDATE gmail5697_projets SET etat='envoi_en_cours', envoi_cle=?, envoi_par=?, envoi_at=?, erreur='', version=version+1, maj=? "
                              "WHERE id=? AND etat='depose' AND version=? AND empreinte_depot=?", (idempotence, owner, _iso(), _iso(), p['id'], p['version'], p['empreinte_depot']))
        if not cur.rowcount:
            raise Stop('envoi_deja_demande')
        _journal(desk, p['id'], 'envoyer', idempotence, owner)
        desk.db.commit()
    except BaseException:
        if desk.db.in_transaction:
            desk.db.rollback()
        raise
    try:
        out = gg._appel_brut('POST', API + '/drafts/send', acces, {'id': p['draft_id']})
    except Stop as error:
        statut, code = getattr(error, 'statut', 0), str(error).split(':')[0]
        if statut in (400, 401, 403, 429):                  # refus certain avant envoi : rien n'est parti ; un nouveau clic reste possible
            _restaurer(desk, p['id'], 'depose', code)
            desk.db.execute("UPDATE gmail5697_journal SET resultat='refuse' WHERE idempotence=?", (idempotence,))
            desk.db.commit()
            raise
        _restaurer(desk, p['id'], 'envoi_a_verifier', code)  # délai, réseau, 5xx, 404 : résultat incertain, verrouillé
        desk.db.execute("UPDATE gmail5697_journal SET resultat='a_verifier' WHERE idempotence=?", (idempotence,))
        desk.db.commit()
        desk.audit('gmail5697_envoi_a_verifier', {'projet': p['id'][:12], 'code': code})
        return {'deja': False, 'resultat': 'a_verifier', **vue_projet(desk, owner, p['id'])}
    desk.db.execute("UPDATE gmail5697_projets SET etat='envoye', envoye_id=?, maj=? WHERE id=?", (str(out.get('id') or ''), _iso(), p['id']))
    desk.db.execute("UPDATE gmail5697_journal SET resultat='envoye' WHERE idempotence=?", (idempotence,))
    desk.db.commit()
    desk.audit('gmail5697_envoye', {'projet': p['id'][:12]})
    return {'deja': False, 'resultat': 'envoye', **vue_projet(desk, owner, p['id'])}


def comparer(desk, owner, projet_id):
    """Brouillon modifié dans Gmail : les deux versions côte à côte (lecture seule)."""
    p = projet(desk, owner, projet_id)
    row = _connexion_active(desk, owner, 'gmail.brouillons')
    distant = _distant(desk, row['id'], p['draft_id']) if p['draft_id'] else None
    return {'local': vue_projet(desk, owner, p['id'])['releve'], 'gmail': distant['releve'] if distant else None}


def resoudre(desk, owner, projet_id, version, choix):
    """Décision explicite après une modification dans Gmail : garder la version de Gmail (reprise dans le projet), ou la remplacer par la
    version locale au prochain dépôt. Jamais d'écrasement sans ce choix."""
    p = projet(desk, owner, projet_id)
    if p['etat'] != 'modifie_a_distance':
        raise Stop('etat_incompatible')
    if int(version) != p['version']:
        raise Stop('version_perimee')
    row = _connexion_active(desk, owner, 'gmail.brouillons')
    distant = _distant(desk, row['id'], p['draft_id']) if p['draft_id'] else None
    if choix == 'garder_gmail':
        if distant is None:
            raise Stop('brouillon_distant_absent')
        r = distant['releve']
        if r['pieces']:
            raise Stop('pieces_ajoutees_dans_gmail')        # les pièces ajoutées dans Gmail ne sont pas reprises en local : à envoyer depuis Gmail
        desk.db.execute("UPDATE gmail5697_projets SET a=?, cc=?, objet=?, texte=?, etat='depose', empreinte_depot=?, erreur='', version=version+1, maj=? WHERE id=?",
                        (', '.join(r['a']), ', '.join(r['cc']), r['objet'], r['texte'], empreinte(r), _iso(), p['id']))
    elif choix == 'remplacer':
        desk.db.execute("UPDATE gmail5697_projets SET etat='local', empreinte_depot=?, draft_id=CASE WHEN ? THEN draft_id ELSE '' END, erreur='', version=version+1, maj=? WHERE id=?",
                        (empreinte(distant['releve']) if distant else '', 1 if distant else 0, _iso(), p['id']))
    else:
        raise Stop('valeur_invalide')
    desk.db.commit()
    return vue_projet(desk, owner, p['id'])


def verifier(desk, owner, projet_id):
    """Rapprochement d'un résultat incertain par l'identifiant du message : dans les envoyés (envoi) ou les brouillons (dépôt). Lecture seule."""
    p = projet(desk, owner, projet_id)
    row = _connexion_active(desk, owner, 'gmail.brouillons')
    if row['id'] != p['connexion_id']:
        raise Stop('compte_modifie')
    q = 'rfc822msgid:' + p['message_id'].strip('<>')
    if p['etat'] == 'envoi_a_verifier':
        out = gg.appeler(desk, row['id'], 'GET', API + '/messages?' + urlencode({'q': q + ' in:sent', 'maxResults': 2}), capacite='gmail.lecture'
                         if 'gmail.lecture' in gg.capacites(row) else 'gmail.brouillons')
        trouves = [m for m in out.get('messages') or [] if m.get('id')]
        if trouves:
            desk.db.execute("UPDATE gmail5697_projets SET etat='envoye', envoye_id=?, erreur='', maj=? WHERE id=?", (str(trouves[0]['id']), _iso(), p['id']))
            desk.db.commit()
            return {'preuve': True, 'message': 'Envoi confirmé : le message figure dans les envoyés de Gmail.', **vue_projet(desk, owner, p['id'])}
        return {'preuve': False, 'message': 'Aucune preuve d’envoi pour l’instant. Rien n’a été renvoyé : vérifiez dans Gmail (Envoyés) avant toute autre action.',
                **vue_projet(desk, owner, p['id'])}
    if p['etat'] == 'depot_a_verifier':
        out = gg.appeler(desk, row['id'], 'GET', API + '/drafts?' + urlencode({'q': q, 'maxResults': 2}), capacite='gmail.brouillons')
        trouves = [d for d in out.get('drafts') or [] if d.get('id')]
        if trouves:
            distant = _distant(desk, row['id'], str(trouves[0]['id']))
            if distant is not None:
                desk.db.execute("UPDATE gmail5697_projets SET etat='depose', draft_id=?, draft_message_id=?, empreinte_depot=?, erreur='', maj=? WHERE id=?",
                                (distant['id'], distant['message_id'], empreinte(distant['releve']), _iso(), p['id']))
                desk.db.commit()
                return {'preuve': True, 'message': 'Brouillon retrouvé dans Gmail : relisez-le avant tout envoi.', **vue_projet(desk, owner, p['id'])}
        _restaurer(desk, p['id'], 'local', '')
        return {'preuve': False, 'message': 'Aucun brouillon trouvé dans Gmail : le projet est resté local, vous pouvez le déposer.', **vue_projet(desk, owner, p['id'])}
    raise Stop('rien_a_verifier')


# ------------------------------------------------------------------------------------------------ préparation par l'IA (C04 v3 l. 292-296, C06 l. 327)
KIND_IA = 'gmail5697_redaction'
IA_ETATS = {'en_cours': 'Préparation par l’IA en cours', 'pret': 'Projet préparé par l’IA, à relire',
            'non_applique': 'Proposition de l’IA non appliquée : votre version est conservée', 'perime': 'Projet périmé : un nouveau message est arrivé pendant la préparation',
            'echec': 'L’IA n’a pas proposé de texte fiable'}
EN_COURS_IA = ('pending', 'running', 'cancel_requested', 'suspended')


def _dernier_du_fil(desk, conn_id, thread_id):
    m = desk.db.execute("SELECT gmail_id FROM gmail5697_messages WHERE connexion_id=? AND thread_id=? AND supprime=0 AND sens='recu' ORDER BY recu DESC, gmail_id DESC LIMIT 1",
                        (conn_id, thread_id)).fetchone()
    return m[0] if m else ''


def demander_ia(desk, owner, role, mail_key, matter='', instruction='', hidden=frozenset()):
    """« Préparer avec l'IA » sur un message Gmail : dossier confirmé, droits relus, politique des données appliquée (modèle local), un seul
    travail par message et par génération. Le projet reste local ; seul l'avocat le dépose."""
    row = _connexion_active(desk, owner)
    parties = str(mail_key or '').split(':')
    if len(parties) != 3 or parties[0] != 'gmail' or parties[1] != row['id']:
        raise Stop('message_absent')
    m = desk.db.execute('SELECT * FROM gmail5697_messages WHERE connexion_id=? AND gmail_id=? AND supprime=0', (row['id'], parties[2])).fetchone()
    if not m:
        raise Stop('message_absent')
    dossiers = dossiers_du_message(desk, row['id'], m['gmail_id'])
    from .messages5697 import principal
    from .common import load_matters
    matter = str(matter or principal(dossiers) or '')
    if not matter:
        raise Stop('dossier_a_confirmer')                   # C04 : jamais d'analyse sans dossier confirmé
    if matter not in {x['id'] for x in load_matters(desk.c)} or (set(dossiers) | {matter}) & frozenset(hidden or ()):
        raise Stop('dossier_reserve' if matter in frozenset(hidden or ()) or set(dossiers) & frozenset(hidden or ()) else 'dossier_a_confirmer')
    if m['sens'] != 'recu' or m['motif']:
        raise Stop('message_sans_reponse')
    instruction = re.sub(r'[ \t]+', ' ', str(instruction or '')).strip()
    if len(instruction) > 4000:
        raise Stop('instruction_trop_longue_4000_maximum')
    from .data_policy5689 import exiger
    exiger(desk.c, 'llm', 'ollama', finalite='courriel_gmail', dossiers=(matter,), labels=('courriels', 'dossier'), local=True, acteur=owner, role=role)
    p_id = preparer(desk, owner, mail_key, matter, '', hidden)['projet']
    if desk.db.in_transaction:
        desk.db.commit()
    desk.db.execute('BEGIN IMMEDIATE')
    try:
        p = dict(desk.db.execute('SELECT * FROM gmail5697_projets WHERE id=?', (p_id,)).fetchone())
        if p['etat'] != 'local':
            raise Stop('etat_incompatible')
        if p['ia_etat'] == 'en_cours' and p['ia_job']:
            statut = desk.db.execute('SELECT status FROM jobs WHERE id=?', (p['ia_job'],)).fetchone()
            if statut and statut[0] in EN_COURS_IA:
                desk.db.rollback()
                return {'projet': p_id, 'job': p['ia_job'], 'deja': True, 'message': 'Une préparation est déjà en cours pour ce message.'}
        generation = int(p['ia_generation']) + 1
        job = desk.enqueue(KIND_IA, {'connexion': row['id'], 'gmail_id': m['gmail_id'], 'projet': p_id, 'generation': generation, 'matter': matter,
                                     'instruction': instruction, 'version': p['version'], 'demande_par': owner, 'demande_role': role}, priority=20, transactional=True)
        desk.db.execute("UPDATE gmail5697_projets SET ia_generation=?, ia_job=?, ia_etat='en_cours', matter=?, maj=? WHERE id=?", (generation, job, matter, _iso(), p_id))
        desk.db.commit()
    except BaseException:
        if desk.db.in_transaction:
            desk.db.rollback()
        raise
    try:
        desk.notify_queued(job, {})
    except Exception:   # noqa: BLE001 — la file est validée ; une notification en panne ne transforme pas la demande en échec
        pass
    desk.audit('gmail5697_ia_demandee', {'projet': p_id[:12], 'job': job})
    return {'projet': p_id, 'job': job, 'deja': False, 'message': 'Préparation demandée au modèle local : le projet arrivera ici, à relire. Rien n’est déposé ni envoyé.'}


def _mail_local(m):
    """Message de la copie locale Gmail, sous la forme attendue par le circuit de rédaction (en-têtes RFC, texte borné ; aucun HTML)."""
    from .mailbox import Mail
    msg = EmailMessage()
    for nom, valeur in (('From', m['de']), ('To', m['a']), ('Cc', m['cc']), ('Subject', m['objet']), ('Message-ID', m['message_id']), ('References', m['refs'])):
        if valeur:
            try:
                msg[nom] = valeur
            except (ValueError, TypeError):
                continue
    msg.set_content(m['texte'] or '')
    try:
        quand = datetime.fromisoformat(m['recu'])
    except ValueError:
        quand = datetime.now(timezone.utc)
    return Mail(m['gmail_id'], 'gmail', BOITE, set(), quand, msg)


def job_ia(desk, args):
    """Travail ``gmail5697_redaction`` : circuit de rédaction commun (``intelligence.prepare_draft``, variantes 5.6.95), modèle local seul.
    Résultat écrit dans le projet local seulement s'il n'a pas été corrigé entre-temps ; nouveau message dans le fil : projet périmé."""
    from . import intelligence
    from .acces5697 import exiger
    from .messages5697 import cle_gmail
    from .model import modele_local_seul
    from .pause5629 import checkpoint
    checkpoint(desk)
    ensure_schema(desk)
    conn, gid, p_id, generation = str(args['connexion']), str(args['gmail_id']), str(args['projet']), int(args['generation'])
    exiger(desk, (args['demande_par'], args.get('demande_role', '')), dossier_id=str(args['matter']), ref='gmail:%s:%s' % (conn, gid))
    p = desk.db.execute('SELECT * FROM gmail5697_projets WHERE id=?', (p_id,)).fetchone()
    if not p or p['ia_generation'] != generation:
        return {'etat': 'remplace'}
    m = desk.db.execute('SELECT * FROM gmail5697_messages WHERE connexion_id=? AND gmail_id=? AND supprime=0', (conn, gid)).fetchone()
    if not m:
        desk.db.execute("UPDATE gmail5697_projets SET ia_etat='echec', erreur='message_absent', maj=? WHERE id=? AND ia_generation=?", (_iso(), p_id, generation))
        desk.db.commit()
        return {'etat': 'echec'}
    dernier = _dernier_du_fil(desk, conn, m['thread_id'])
    report = {'matter': str(args['matter']), 'reply_recipients': [m['de_adresse']], 'subject': m['objet'], 'sender': m['de_adresse'],
              'manual_request': {'by': args['demande_par'], 'at': _iso(), 'instruction': str(args.get('instruction') or '')[:500]}}
    cle = cle_gmail(conn, gid)
    from .prudent5667 import enabled as _a_completer
    try:
        with modele_local_seul():                       # C06 : contenu Gmail analysé par le modèle local autorisé, sans repli distant
            prepared = intelligence.prepare_draft(desk, {'key': cle, 'style': 'prudent', 'instruction': str(args.get('instruction') or ''),
                                                         'mode_projet': 'a_completer' if _a_completer(desk) else '', '_source': (report, _mail_local(m))})
        ligne = desk.db.execute('SELECT data FROM manual_drafts WHERE mail_key=?', (cle,)).fetchone()
        data = json.loads(ligne[0]) if ligne else {'result': {'body': '', 'limits': []}}
    finally:
        desk.db.execute('DELETE FROM manual_drafts WHERE mail_key=?', (cle,))   # le projet Gmail garde le résultat ; jamais un dépôt IMAP
        desk.db.commit()
    checkpoint(desk)                                    # pause ou arrêt pendant la génération : rien n'est écrit
    resultat = data.get('result') or {}
    corps = str(resultat.get('body') or '')
    limites = json.dumps([str(x)[:400] for x in (resultat.get('limits') or [])][:20], ensure_ascii=False)
    p = dict(desk.db.execute('SELECT * FROM gmail5697_projets WHERE id=?', (p_id,)).fetchone())
    if p['ia_generation'] != generation:
        return {'etat': 'remplace'}
    perime = _dernier_du_fil(desk, conn, m['thread_id']) != dernier
    if not corps:
        etat, champs = 'echec', {'texte_ia': '', 'ia_limites': limites}
    elif p['corrige'] or p['version'] != int(args['version']) or p['etat'] != 'local':
        etat, champs = 'non_applique', {'texte_ia': corps, 'ia_limites': limites}   # jamais de remplacement d'une version de l'avocat
    elif perime:
        etat, champs = 'perime', {'texte': corps, 'texte_ia': corps, 'ia_limites': limites, 'perime': 1}
    else:
        etat, champs = 'pret', {'texte': corps, 'texte_ia': corps, 'ia_limites': limites}
    champs.update(ia_etat=etat, maj=_iso())
    cur = desk.db.execute('UPDATE gmail5697_projets SET %s%s WHERE id=? AND ia_generation=? AND version=?' % (
        ','.join('%s=?' % k for k in champs), ', version=version+1' if 'texte' in champs else ''), list(champs.values()) + [p_id, generation, p['version']])
    desk.db.commit()
    if not cur.rowcount:
        return {'etat': 'remplace'}
    desk.audit('gmail5697_ia_terminee', {'projet': p_id[:12], 'etat': etat})
    return {'etat': etat, 'projet_prepare': bool(prepared.get('projet_prepare')), 'depot_automatique': False}
