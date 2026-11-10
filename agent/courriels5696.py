"""5.6.96 (cahier v3, C01 à C04 sur IMAP, I03) : vue « À traiter avec le Pilote » de Courriels (``/courriels?vue=assistante``).

La vue est **additive** (C07) : « À relire » et « Boîte de réception » ne changent pas, leurs adresses non plus. Elle est derrière une option
**désactivée par défaut**, réglée pour le cabinet (administrateur) et, si la personne le souhaite, pour son seul compte. Désactiver l'option rend
l'usage courant sans rien effacer : les suivis, décisions et projets restent dans leurs tables.

- **Projection locale** : la liste est lue dans la base (``work_items`` du collecteur, fil connu de l'état de l'agent, registre des documents
  préparés, suivis de cette vue). L'affichage ne fait ni appel IMAP, ni appel au modèle, ni écriture ; changer de vue ne lance ni analyse ni
  synchronisation. Une ligne par (cabinet, compte, boîte, fil) : le dernier message reçu du fil la représente.
- **Même identité qu'Aujourd'hui** : la tâche est le ``work_items`` du message (clé ``mail_key``) ; le projet est l'élément du registre
  ``prepared_items5689`` dont ``response_id`` est cette clé (« Relire la réponse » ouvre ``/livrables/<document_id>``, comme Aujourd'hui). Rien
  n'est compté deux fois : la vue n'ajoute aucun compteur, et « Terminer » / « Écarter » passent la tâche en ``handled`` / ``archived``.
- **États** : À traiter, En préparation, Réponse prête, À compléter, En attente, Reporté, Terminé, Écarté. Chaque décision passe par un verrou de
  version (409 si la page est périmée) et une clé d'idempotence (deux clics : une seule décision) ; elle est journalisée et peut être annulée
  tant qu'aucune décision ne l'a suivie. Ces gestes agissent sur la tâche, jamais sur le message distant (ni drapeau, ni déplacement).
- **Fournisseur de boîte** (C03) : ``FournisseurIMAP`` expose seulement ``lister``, ``lire_message``, ``lire_fil`` et ``lire_piece`` ; l'accès est
  vérifié avant chaque appel (dossier réservé, identité du message par UIDVALIDITY/UID). La demande de réponse est une capacité distincte,
  ``demander_reponse``, jamais donnée à l'analyse : elle passe par le circuit existant (``boite5625.request_reply`` → ``prepare_reply``,
  rédaction 5.6.95), un seul travail par message et par génération. Aucun envoi, aucun dépôt ici. Gmail viendra en 5.6.97 derrière ce contrat.
"""
from datetime import datetime, timedelta, timezone
import json
import re
import sqlite3
import uuid
from zoneinfo import ZoneInfo

from .common import Stop, load_matters, matter_display

CLE_CABINET = 'courriels5696:vue_assistee'
CLE_COMPTE = 'courriels5696:vue_assistee:'          # + compte de la personne : True, False ou absent (« comme le cabinet »)
STATUTS = ('a_traiter', 'en_preparation', 'reponse_prete', 'a_completer', 'en_attente', 'reporte', 'termine', 'ecarte')
LIBELLES = {'a_traiter': 'À traiter', 'en_preparation': 'En préparation', 'reponse_prete': 'Réponse prête', 'a_completer': 'À compléter',
            'en_attente': 'En attente', 'reporte': 'Reporté', 'termine': 'Terminé', 'ecarte': 'Écarté'}
# filtres de la liste : (clé, libellé, statuts) ; le premier est l'affichage par défaut
FILTRES = (('actifs', 'À traiter et réponses prêtes', ('a_traiter', 'a_completer', 'en_preparation', 'reponse_prete')),
           ('a_traiter', 'À traiter', ('a_traiter', 'a_completer', 'en_preparation')), ('reponses', 'Réponses prêtes', ('reponse_prete',)),
           ('attente', 'En attente', ('en_attente',)), ('reportes', 'Reportés', ('reporte',)), ('termines', 'Terminés et écartés', ('termine', 'ecarte')))
# état du collecteur (``work_items.state``) -> état affiché, tant qu'aucune décision de cette vue ne s'applique
DEPUIS_TACHE = {'needs_action': 'a_traiter', 'needs_confirmation': 'a_completer', 'drafting': 'en_preparation', 'processing': 'en_preparation',
                'draft_ready': 'reponse_prete', 'handled': 'termine', 'archived': 'termine', 'backlog': 'reporte'}
# décision -> état de la tâche commune (Aujourd'hui) ; les autres décisions ne la touchent pas
TACHE = {'terminer': 'handled', 'ecarter': 'archived'}
ACTIONS = ('preparer', 'reporter', 'attendre', 'terminer', 'ecarter', 'rouvrir', 'annuler')
EN_COURS = ('pending', 'running', 'cancel_requested', 'suspended')
LIMITE = 300                     # lignes lues dans la projection (les plus récentes), au plus
FENETRE_JOURS = 60               # messages reçus depuis ; un fil déjà suivi reste lu quel que soit son âge
TEXTE_MAX = 6000                 # caractères d'un message lu dans le fil
FIL_MAX = 12                     # messages d'un fil rendus au plus
IDEMPOTENCE = re.compile(r'^[A-Za-z0-9-]{8,64}$')
FIL = re.compile(r'^[0-9a-f]{64}$')

SCHEMA = '''
CREATE TABLE IF NOT EXISTS courriels5696_suivis(id TEXT PRIMARY KEY, cabinet_id TEXT NOT NULL DEFAULT 'cabinet', compte TEXT NOT NULL, boite TEXT NOT NULL,
    fil TEXT NOT NULL, mail_key TEXT NOT NULL, matter TEXT NOT NULL DEFAULT '', statut TEXT NOT NULL, motif TEXT NOT NULL DEFAULT '',
    attente_de TEXT NOT NULL DEFAULT '', echeance TEXT NOT NULL DEFAULT '', fuseau TEXT NOT NULL DEFAULT '', job_id INTEGER, generation INTEGER NOT NULL DEFAULT 0,
    version INTEGER NOT NULL DEFAULT 1, auteur TEXT NOT NULL DEFAULT '', cree TEXT NOT NULL, maj TEXT NOT NULL, UNIQUE(cabinet_id, compte, boite, fil));
CREATE INDEX IF NOT EXISTS courriels5696_suivis_cle ON courriels5696_suivis(mail_key);
CREATE TABLE IF NOT EXISTS courriels5696_evenements(id TEXT PRIMARY KEY, suivi_id TEXT NOT NULL, idempotence TEXT NOT NULL UNIQUE, action TEXT NOT NULL,
    ancien TEXT NOT NULL, nouveau TEXT NOT NULL, motif TEXT NOT NULL DEFAULT '', mail_key TEXT NOT NULL DEFAULT '', tache_avant TEXT NOT NULL DEFAULT '',
    tache_apres TEXT NOT NULL DEFAULT '', job_id INTEGER, auteur TEXT NOT NULL, at TEXT NOT NULL, annule TEXT NOT NULL DEFAULT '');
CREATE INDEX IF NOT EXISTS courriels5696_evenements_suivi ON courriels5696_evenements(suivi_id, at);
'''


def ensure_schema(desk):
    desk.db.executescript(SCHEMA)


def _maintenant():
    return datetime.now(timezone.utc)


# ------------------------------------------------------------------------------------------------ option (C07 : désactivée par défaut)
def option(desk, owner='cabinet'):
    """(cabinet, compte, effective) : le choix du compte, s'il existe, prime ; sinon celui du cabinet ; sinon désactivée."""
    try:
        cabinet = desk.settings(CLE_CABINET, False) is True
        compte = desk.settings(CLE_COMPTE + str(owner), None) if owner else None
    except sqlite3.Error:         # base occupée : l'usage courant, jamais une vue à moitié
        return False, None, False
    compte = compte if isinstance(compte, bool) else None
    return cabinet, compte, (compte if compte is not None else cabinet)


def active(desk, owner='cabinet'):
    return option(desk, owner)[2]


def regler(desk, owner, role, data):
    """Réglage de l'option : ``cabinet`` (administrateur seulement) et ``compte`` (« oui », « non » ou « cabinet » = comme le cabinet). Ne touche
    à aucun suivi : désactiver rend l'usage courant, réactiver retrouve les décisions telles qu'elles étaient."""
    if 'cabinet' in data:
        if role != 'administrateur':
            raise Stop('role_insuffisant')
        desk.setting(CLE_CABINET, data['cabinet'] is True)
    if 'compte' in data:
        valeur = str(data.get('compte') or '')
        if valeur not in ('oui', 'non', 'cabinet'):
            raise Stop('valeur_invalide')
        if valeur == 'cabinet':
            desk.db.execute('DELETE FROM settings WHERE key=?', (CLE_COMPTE + str(owner),))
            desk.db.commit()
        else:
            desk.setting(CLE_COMPTE + str(owner), valeur == 'oui')
    cabinet, compte, effective = option(desk, owner)
    desk.audit('courriels5696_option', {'cabinet': cabinet, 'compte': compte})
    return {'cabinet': cabinet, 'compte': compte, 'active': effective,
            'message': 'Vue « À traiter avec le Pilote » %s pour votre compte. Aucune décision ni aucun projet n’a été effacé.' % ('activée' if effective else 'désactivée')}


# ------------------------------------------------------------------------------------------------ projection locale
def _compte(cfg):
    return str(cfg.get('username') or '') + '@' + str(cfg.get('host') or '')


def _adresse(cfg):
    return str(cfg.get('from_address') or cfg.get('username') or '')


def _fuseau(desk, owner):
    from .acces5683 import today_of
    return today_of(desk, owner)[1]


def _fils(desk, cles):
    """Fil de chaque message, tel que l'agent l'a noté (``digest(compte + racine des références)``) ; à défaut, le message seul."""
    if not cles:
        return {}
    try:
        from .state import State
        state = State(desk.c['state_dir'])
    except Exception:   # noqa: BLE001 — état de l'agent illisible : un message = un fil, jamais une fusion par objet
        return {}
    try:
        out = {}
        for i in range(0, len(cles), 400):
            part = cles[i:i + 400]
            out.update({r[0]: r[1] for r in state.db.execute('SELECT key, thread FROM messages WHERE key IN (%s)' % ','.join('?' * len(part)), part)})
        return out
    finally:
        state.db.close()


def _prepares(desk, cles):
    """Projets du registre commun par message source (``response_id``) : la même carte qu'Aujourd'hui et que « À relire »."""
    if not cles:
        return {}
    try:
        rows = desk.db.execute("SELECT response_id, document_id, review_state, deposit_state FROM prepared_items5689 WHERE lifecycle='current' AND response_id IN (%s) ORDER BY version_number"
                               % ','.join('?' * len(cles)), cles).fetchall()
    except sqlite3.Error:         # registre pas encore créé
        return {}
    return {r['response_id']: dict(r) for r in rows}


def _suivis(desk):
    ensure_schema(desk)
    return {(r['compte'], r['boite'], r['fil']): dict(r) for r in desk.db.execute('SELECT * FROM courriels5696_suivis')}


def _jobs(desk, ids):
    ids = [int(i) for i in ids if i]
    if not ids:
        return {}
    return {r[0]: r[1] for r in desk.db.execute('SELECT id, status FROM jobs WHERE id IN (%s)' % ','.join('?' * len(ids)), ids)}


def _instant_de(valeur):
    """Instant d'une date ISO (décalage du serveur IMAP ou UTC) ; illisible : le plus ancien possible, jamais une erreur d'affichage."""
    try:
        quand = datetime.fromisoformat(str(valeur or '').replace('Z', '+00:00'))
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)
    return quand if quand.tzinfo else quand.replace(tzinfo=timezone.utc)


def _echu(valeur, maintenant):
    try:
        quand = datetime.fromisoformat(str(valeur))
    except ValueError:
        return False
    return (quand if quand.tzinfo else quand.replace(tzinfo=timezone.utc)) <= maintenant


def statut_de(tache, suivi, projet, nouveau, job, maintenant):
    """(état, précision) d'une ligne. ``tache`` : état du collecteur ; ``suivi`` : décision de cette vue ; ``nouveau`` : un message reçu
    après la décision (une resynchronisation du même message ne compte pas) ; ``job`` : état du travail de préparation demandé ici."""
    base = DEPUIS_TACHE.get(tache, 'a_traiter')
    if projet and base in ('a_traiter', 'a_completer', 'en_preparation'):
        base = 'reponse_prete'
    if not suivi:
        return base, ''
    s = suivi['statut']
    if nouveau:                   # même ligne rouverte avec le nouveau contexte (règle par défaut : rouvrir)
        return ('reponse_prete' if projet else 'a_traiter'), 'Nouveau message dans le fil'
    if s == 'reporte' and suivi.get('echeance') and _echu(suivi['echeance'], maintenant):
        return 'a_traiter', 'Report arrivé à échéance'
    if s == 'en_attente' and suivi.get('echeance') and _echu(suivi['echeance'], maintenant):
        return 'a_traiter', 'Attente arrivée à échéance' + (' (%s)' % suivi['attente_de'] if suivi.get('attente_de') else '')
    if s == 'en_preparation':
        if projet:
            return 'reponse_prete', ''
        if job and job not in EN_COURS:
            return 'a_completer', 'La préparation s’est terminée sans projet : voir le motif dans « Pourquoi ? »'
    return s, ''


def lignes(desk, owner='cabinet', hidden=frozenset(), filtre='actifs', dossier='', boite=''):
    """Lignes de la vue, lues localement (aucun réseau, aucune écriture). Un dossier réservé à d'autres membres n'apparaît pas ; le courrier
    du cabinet (copies, envoyés) n'est jamais une demande à traiter."""
    from .mailbox import own_addresses
    cfg = desk.c.get('mail') or {}
    compte, nom_boite = _compte(cfg), str(cfg.get('inbox') or 'INBOX')
    propres = {a.lower() for a in own_addresses(cfg)}
    maintenant = _maintenant()
    depuis = (maintenant - timedelta(days=FENETRE_JOURS)).isoformat()
    suivis = _suivis(desk)
    suivies = [s['mail_key'] for s in suivis.values()]
    try:
        rows = [dict(r) for r in desk.db.execute(
            'SELECT mail_key, state, source_status, source_reason, matter, subject, sender, received FROM work_items WHERE received>=? OR mail_key IN (SELECT mail_key FROM courriels5696_suivis) '
            'ORDER BY received DESC LIMIT ?', (depuis, LIMITE))]
    except sqlite3.Error:
        rows = []
    fils = _fils(desk, [r['mail_key'] for r in rows] + suivies)
    labels = {m['id']: matter_display(m) for m in load_matters(desk.c)}
    groupes = {}
    for r in rows:
        if r['state'] == 'ignored' and r['mail_key'] not in suivies:      # notification, liste, copie : écartés par l'agent, hors de la vue
            continue
        if str(r['sender'] or '').lower() in propres:
            continue
        fil = fils.get(r['mail_key']) or r['mail_key']
        courant = groupes.get(fil)
        if courant is None or _instant_de(r['received']) > _instant_de(courant['received']):
            groupes[fil] = r
    projets = _prepares(desk, [r['mail_key'] for r in groupes.values()])
    jobs = _jobs(desk, [s.get('job_id') for s in suivis.values()])
    statuts = STATUTS if filtre == 'tous' else (dict((k, v) for k, _, v in FILTRES).get(filtre) or FILTRES[0][2])
    out = []
    for fil, r in groupes.items():
        suivi = suivis.get((compte, nom_boite, fil))
        matter = (suivi or {}).get('matter') or r['matter'] or ''
        if matter in hidden or (dossier and matter != dossier) or (boite and boite != nom_boite):
            continue
        projet = projets.get(r['mail_key'])
        nouveau = bool(suivi and suivi['mail_key'] != r['mail_key'] and _instant_de(r['received']) > _instant_de(suivi['maj']))
        statut, precision = statut_de(r['state'], suivi, projet, nouveau, jobs.get((suivi or {}).get('job_id')), maintenant)
        if statut not in statuts:
            continue
        out.append({'fil': fil, 'mail_key': r['mail_key'], 'compte': compte, 'adresse': _adresse(cfg), 'boite': nom_boite, 'de': r['sender'] or '',
                    'objet': r['subject'] or '(sans objet)', 'recu': r['received'] or '', 'matter': matter, 'dossier': labels.get(matter, ''),
                    'statut': statut, 'libelle': LIBELLES[statut], 'precision': precision, 'version': (suivi or {}).get('version', 0),
                    'tache': r['state'], 'raison': r['source_reason'] or '', 'projet': (projet or {}).get('document_id', ''),
                    'suivi': {k: (suivi or {}).get(k, '') for k in ('statut', 'motif', 'attente_de', 'echeance', 'fuseau', 'auteur', 'maj')} if suivi else None})
    out.sort(key=lambda x: _instant_de(x['recu']), reverse=True)
    return out


def ligne(desk, fil, owner='cabinet', hidden=frozenset(), cle=''):
    """Ligne d'un fil (toutes catégories), ou celle du message ``cle`` (lien venu d'Aujourd'hui) ; dossier réservé : refus nommé."""
    if cle and not fil:
        fils = _fils(desk, [str(cle)])
        fil = fils.get(str(cle)) or str(cle)
    if not FIL.match(str(fil or '')):
        raise Stop('fil_invalide')
    for r in lignes(desk, owner, frozenset(), 'tous'):
        if r['fil'] == fil:
            if r['matter'] in hidden:
                raise Stop('dossier_reserve')
            return r
    raise Stop('fil_absent')


def evenements(desk, suivi_id):
    ensure_schema(desk)
    return [dict(r) for r in desk.db.execute('SELECT action, ancien, nouveau, motif, auteur, at, annule FROM courriels5696_evenements WHERE suivi_id=? ORDER BY at DESC, rowid DESC LIMIT 20', (suivi_id,))]


def suivi_id(desk, fil):
    ensure_schema(desk)
    cfg = desk.c.get('mail') or {}
    row = desk.db.execute('SELECT id FROM courriels5696_suivis WHERE cabinet_id=? AND compte=? AND boite=? AND fil=?', ('cabinet', _compte(cfg), str(cfg.get('inbox') or 'INBOX'), fil)).fetchone()
    return row[0] if row else ''


# ------------------------------------------------------------------------------------------------ fournisseur de boîte (C03)
class FournisseurIMAP:
    """Contrat de lecture d'une boîte : ``lister``, ``lire_message``, ``lire_fil``, ``lire_piece``. Lecture seule (``BODY.PEEK``, boîte ouverte
    en ``EXAMINE``) ; aucune méthode d'écriture. Référence d'un message : (compte, dossier, UIDVALIDITY, UID) et sa clé ``mail_key`` ; une clé
    qui ne correspond plus (UID réutilisé, boîte réindexée) est refusée au lieu de montrer un autre message."""
    nom = 'imap'
    CAPACITES = ('lister', 'lire_message', 'lire_fil', 'lire_piece')

    def __init__(self, desk, hidden=frozenset(), box=None):
        self.desk, self.hidden, self._box = desk, frozenset(hidden or ()), box
        self.cfg = desk.c.get('mail') or {}

    def _acces(self, matter):
        """Contrôle avant chaque appel : dossier réservé, messagerie configurée."""
        if matter and matter in self.hidden:
            raise Stop('dossier_reserve')
        if not self.cfg.get('host') or self.cfg.get('enabled') is False:     # messagerie absente ou coupée : aucune connexion tentée
            raise Stop('messagerie_non_configuree')

    def _boite(self):
        from . import mailbox as mbx
        if self._box is None:
            self._box = mbx.Mailbox(self.cfg)
        return self._box

    def fermer(self):
        if self._box is not None:
            try:
                self._box.close()
            except Exception:   # noqa: BLE001
                pass
            self._box = None

    def reference(self, mail_key):
        from .desk import report_for
        report = report_for(self.desk.c, str(mail_key))
        return {'compte': _compte(self.cfg), 'dossier': str(report.get('source_mailbox') or self.cfg.get('inbox') or 'INBOX'), 'uid': str(report.get('source_uid') or ''),
                'mail_key': str(mail_key), 'matter': str(report.get('matter') or '')}

    def _message(self, ref, corps=True):
        self._acces(ref.get('matter'))
        mail = self._boite().fetch(ref['dossier'], ref['uid'], headers_only=not corps)
        if mail.key(_compte(self.cfg)) != ref['mail_key']:
            raise Stop('message_deplace_ou_boite_reindexee')
        return mail

    def _vue(self, mail, propres):
        from .boite5625 import _attachments
        texte = str(mail.text or '')
        sens = 'envoye' if str(mail.sender or '').lower() in propres else 'recu'
        return {'uid': mail.uid, 'uidvalidity': mail.uidvalidity, 'dossier': mail.mailbox, 'de': str(mail.msg.get('From', ''))[:300], 'a': str(mail.msg.get('To', ''))[:500],
                'cc': str(mail.msg.get('Cc', ''))[:500], 'objet': mail.subject[:300], 'date': mail.timestamp.isoformat(), 'sens': sens,
                'texte': texte[:TEXTE_MAX], 'tronque': len(texte) > TEXTE_MAX, 'pieces': [{k: p[k] for k in ('index', 'name', 'type', 'size')} for p in _attachments(mail.msg)]}

    def lister(self, boite='', curseur=1):
        self._acces('')
        from .boite5625 import listing
        return listing(self.desk, page=curseur or 1, box=self._boite(), folder=boite or None)

    def lire_message(self, ref):
        from .mailbox import own_addresses
        return self._vue(self._message(ref), {a.lower() for a in own_addresses(self.cfg)})

    def lire_fil(self, ref):
        """Messages du fil (réception et envoyés), du plus ancien au plus récent, texte brut borné : jamais de HTML exécuté ni d'image distante."""
        from .mailbox import own_addresses
        propres = {a.lower() for a in own_addresses(self.cfg)}
        mail = self._message(ref)
        try:
            autres = self._boite().thread(mail)
        except Stop as error:            # fil trop long ou historique trop volumineux : le dernier message seul, annoncé
            return {'messages': [self._vue(mail, propres)], 'limite': str(error)}
        messages = sorted(autres + [mail], key=lambda m: m.timestamp)[-FIL_MAX:]
        return {'messages': [self._vue(m, propres) for m in messages], 'limite': '' if len(autres) + 1 <= FIL_MAX else 'fil_borne_%d' % FIL_MAX}

    def lire_piece(self, ref, index):
        self._acces(ref.get('matter'))
        self._message(ref, corps=False)
        from .boite5625 import attachment_text
        return attachment_text(self.desk, ref['uid'], index, box=self._boite(), folder=ref['dossier'])


def demander_reponse(desk, ref, matter, instruction, owner):
    """Capacité d'écriture distincte du contrat de lecture : demande de projet par le circuit de rédaction existant. Rien n'est envoyé ni déposé ici."""
    from . import boite5625
    return boite5625.request_reply(desk, ref['uid'], matter, instruction, owner=owner, folder=ref['dossier'], without_matter=not matter)


# ------------------------------------------------------------------------------------------------ décisions
def _texte(valeur, limite):
    return re.sub(r'\s+', ' ', str(valeur or '')).strip()[:limite]


def _instant(valeur, zone):
    """« AAAA-MM-JJTHH:MM » (champ date et heure) ou « AAAA-MM-JJ » saisi en heure locale du compte -> instant avec son décalage."""
    texte = str(valeur or '').strip()
    try:
        quand = datetime.fromisoformat(texte if 'T' in texte else texte + 'T09:00')
    except ValueError:
        raise Stop('date_invalide') from None
    return (quand if quand.tzinfo else quand.replace(tzinfo=ZoneInfo(zone))).isoformat(timespec='minutes')


def decider(desk, owner, fil, action, version, idempotence, data=None, hidden=frozenset()):
    """Décision sur une ligne, sous verrou de version et clé d'idempotence. Retourne la ligne mise à jour et un message.
    Une même clé rejouée rend la première décision sans nouvel effet ; une version périmée lève ``version_perimee`` (409)."""
    data = data or {}
    if action not in ACTIONS:
        raise Stop('action_inconnue')
    if not IDEMPOTENCE.match(str(idempotence or '')):
        raise Stop('idempotence_requise')
    try:
        version = int(version)
    except (TypeError, ValueError):
        raise Stop('version_requise') from None
    ensure_schema(desk)
    deja = desk.db.execute('SELECT e.*, s.version AS v FROM courriels5696_evenements e JOIN courriels5696_suivis s ON s.id=e.suivi_id WHERE e.idempotence=?', (str(idempotence),)).fetchone()
    if deja:                       # double clic ou nouvel essai réseau : la décision déjà prise, rien de plus
        return {'deja': True, 'statut': deja['nouveau'], 'version': deja['v'], 'job_id': deja['job_id'], 'message': 'Décision déjà enregistrée : rien de plus n’a été fait.'}
    courante = ligne(desk, fil, owner, hidden)
    if courante['version'] != version:      # page périmée : le conflit d'abord, avant tout contrôle d'état (revérifié sous verrou)
        raise Stop('version_perimee')
    zone = _fuseau(desk, owner)
    motif, extra = _texte(data.get('motif'), 500), {}
    if action == 'ecarter' and len(motif) < 3:
        raise Stop('motif_requis')
    if action == 'reporter':
        extra = {'echeance': _instant(data.get('jusqua'), zone), 'fuseau': zone}
        if datetime.fromisoformat(extra['echeance']) <= _maintenant():
            raise Stop('date_passee')
    if action == 'attendre':
        de = _texte(data.get('de_qui'), 120)
        if len(de) < 2:
            raise Stop('de_qui_requis')
        extra = {'attente_de': de, 'echeance': _instant(data.get('jusqua'), zone), 'fuseau': zone}
    if action == 'preparer' and courante['statut'] not in ('a_traiter', 'a_completer'):
        raise Stop('preparation_deja_demandee' if courante['statut'] == 'en_preparation' else 'reponse_deja_prete' if courante['statut'] == 'reponse_prete' else 'etat_incompatible')
    if action == 'rouvrir' and courante['statut'] not in ('termine', 'ecarte', 'reporte', 'en_attente'):
        raise Stop('etat_incompatible')
    if action in ('reporter', 'attendre', 'terminer', 'ecarter') and courante['statut'] in ('termine', 'ecarte') and action != 'ecarter':
        raise Stop('etat_incompatible')
    cfg = desk.c.get('mail') or {}
    compte, boite = _compte(cfg), str(cfg.get('inbox') or 'INBOX')
    stamp = _maintenant().isoformat()
    desk.db.execute('BEGIN IMMEDIATE')
    try:
        row = desk.db.execute('SELECT * FROM courriels5696_suivis WHERE cabinet_id=? AND compte=? AND boite=? AND fil=?', ('cabinet', compte, boite, fil)).fetchone()
        actuelle = row['version'] if row else 0
        if actuelle != version:
            raise Stop('version_perimee')
        ancien = courante['statut']
        a_arreter = None
        if action == 'annuler':
            nouveau, tache_avant, tache_apres, a_arreter = _annulation(desk, row)
        else:
            nouveau = {'preparer': 'en_preparation', 'reporter': 'reporte', 'attendre': 'en_attente', 'terminer': 'termine', 'ecarter': 'ecarte',
                       'rouvrir': 'a_traiter'}[action]
            tache_avant = courante['tache']
            tache_apres = TACHE.get(action) or (_tache_rouverte(desk, row) if action == 'rouvrir' else '')
        ident = row['id'] if row else uuid.uuid4().hex
        valeurs = {'statut': nouveau, 'motif': motif if action in ('ecarter', 'terminer', 'reporter', 'attendre') else '', 'attente_de': extra.get('attente_de', ''),
                   'echeance': extra.get('echeance', ''), 'fuseau': extra.get('fuseau', ''), 'mail_key': courante['mail_key'], 'matter': courante['matter'], 'auteur': owner, 'maj': stamp}
        if row:
            desk.db.execute('UPDATE courriels5696_suivis SET statut=?, motif=?, attente_de=?, echeance=?, fuseau=?, mail_key=?, matter=?, auteur=?, maj=?, version=version+1 WHERE id=?',
                            tuple(valeurs.values()) + (ident,))
        else:
            desk.db.execute('INSERT INTO courriels5696_suivis(id, cabinet_id, compte, boite, fil, mail_key, matter, statut, motif, attente_de, echeance, fuseau, auteur, cree, maj, version) '
                            'VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1)', (ident, 'cabinet', compte, boite, fil, valeurs['mail_key'], valeurs['matter'], nouveau, valeurs['motif'],
                                                                         valeurs['attente_de'], valeurs['echeance'], valeurs['fuseau'], owner, stamp, stamp))
        if tache_apres and tache_apres != tache_avant:
            desk.db.execute('UPDATE work_items SET state=?, updated=?, resolved=? WHERE mail_key=?',
                            (tache_apres, stamp, stamp if tache_apres in ('handled', 'archived', 'draft_ready') else None, courante['mail_key']))
        evenement = uuid.uuid4().hex
        desk.db.execute('INSERT INTO courriels5696_evenements(id, suivi_id, idempotence, action, ancien, nouveau, motif, mail_key, tache_avant, tache_apres, auteur, at) '
                        'VALUES(?,?,?,?,?,?,?,?,?,?,?,?)', (evenement, ident, str(idempotence), action, ancien, nouveau, motif, courante['mail_key'],
                                                             tache_avant if tache_apres else '', tache_apres, owner, stamp))
        desk.db.commit()
    except BaseException:
        if desk.db.in_transaction:
            desk.db.rollback()
        raise
    job = None
    if a_arreter:                  # annuler « Préparer » : le travail encore en file est arrêté par le circuit existant
        desk.cancel_job(a_arreter)
    if action == 'preparer':
        job = _preparer(desk, owner, ident, evenement, courante, data, {'row': dict(row) if row else None, 'version': actuelle})
    desk.audit('courriels5696_decision', {'action': action, 'fil': fil[:16], 'statut': nouveau, 'job': job})
    nouvelle = desk.db.execute('SELECT version FROM courriels5696_suivis WHERE id=?', (ident,)).fetchone()[0]
    return {'deja': False, 'statut': nouveau, 'version': nouvelle, 'job_id': job, 'message': MESSAGES.get(action, 'Décision enregistrée.'), 'noreload': False}


MESSAGES = {'preparer': 'Préparation demandée : le projet arrivera dans « Réponse prête ». Rien n’est envoyé.',
            'reporter': 'Suivi reporté. Le message n’a pas été déplacé.', 'attendre': 'Suivi mis en attente. Le message n’a pas été déplacé.',
            'terminer': 'Suivi terminé. Le message n’a pas été déplacé.', 'ecarter': 'Suivi écarté avec son motif. Le message n’a pas été déplacé.',
            'rouvrir': 'Suivi rouvert.', 'annuler': 'Dernière décision annulée.'}


def _tache_rouverte(desk, row):
    """Rouvrir : la tâche reprend l'état qu'elle avait avant « Terminer » ou « Écarter », s'il est connu."""
    if not row:
        return ''
    e = desk.db.execute("SELECT tache_avant FROM courriels5696_evenements WHERE suivi_id=? AND action IN ('terminer','ecarter') AND annule='' AND tache_avant<>'' ORDER BY at DESC, rowid DESC LIMIT 1",
                        (row['id'],)).fetchone()
    return e[0] if e else ''


def _annulation(desk, row):
    """Annule la dernière décision non annulée, seulement si aucune autre ne l'a suivie (sinon : ``decision_posterieure``)."""
    if not row:
        raise Stop('rien_a_annuler')
    dernier = desk.db.execute("SELECT * FROM courriels5696_evenements WHERE suivi_id=? AND action<>'annuler' ORDER BY at DESC, rowid DESC LIMIT 1", (row['id'],)).fetchone()
    if not dernier or dernier['annule']:
        raise Stop('rien_a_annuler')
    arreter = None
    if dernier['action'] == 'preparer' and dernier['job_id']:
        statut = desk.db.execute('SELECT status FROM jobs WHERE id=?', (dernier['job_id'],)).fetchone()
        if not statut or statut[0] not in EN_COURS:   # le projet existe peut-être déjà : on ne prétend pas l'effacer
            raise Stop('preparation_deja_faite')
        arreter = dernier['job_id']
    desk.db.execute("UPDATE courriels5696_evenements SET annule=? WHERE id=?", (_maintenant().isoformat(), dernier['id']))
    return dernier['ancien'], dernier['tache_apres'], dernier['tache_avant'], arreter


def _preparer(desk, owner, ident, evenement, courante, data, avant):
    """Un seul travail par message et par génération : la réservation (événement + statut) est validée avant l'appel ; la file elle-même
    rejoint un travail identique encore en attente. Un échec (messagerie injoignable, dossier inconnu) rend exactement l'état d'avant,
    sans événement, pour qu'un nouvel essai avec la même page reste possible ; rien n'est écrasé si une autre décision a suivi entre-temps."""
    fournisseur = FournisseurIMAP(desk)
    try:
        ref = fournisseur.reference(courante['mail_key'])
        matter = str(data.get('dossier') or courante['matter'] or '')
        if matter and matter not in {m['id'] for m in load_matters(desk.c)}:
            raise Stop('dossier_a_choisir')
        fournisseur._acces(matter)           # dossier réservé, messagerie configurée : contrôlés avant toute connexion
        out = demander_reponse(desk, ref, matter, _texte(data.get('instruction'), 4000), owner)
    except BaseException:
        reservee = desk.db.execute('SELECT version FROM courriels5696_suivis WHERE id=?', (ident,)).fetchone()
        if reservee and reservee[0] == avant['version'] + 1:
            desk.db.execute('DELETE FROM courriels5696_evenements WHERE id=?', (evenement,))
            if avant['row'] is None:
                desk.db.execute('DELETE FROM courriels5696_suivis WHERE id=?', (ident,))
            else:
                desk.db.execute('UPDATE courriels5696_suivis SET statut=?, mail_key=?, auteur=?, maj=?, version=? WHERE id=?',
                                (avant['row']['statut'], avant['row']['mail_key'], avant['row']['auteur'], avant['row']['maj'], avant['version'], ident))
            desk.db.commit()
        raise
    finally:
        fournisseur.fermer()
    job = int(out['job_id'])
    desk.db.execute('UPDATE courriels5696_suivis SET job_id=?, generation=generation+1 WHERE id=?', (job, ident))
    desk.db.execute('UPDATE courriels5696_evenements SET job_id=? WHERE id=?', (job, evenement))
    desk.db.commit()
    return job


def raison_lisible(code):
    """« Pourquoi ? » : motif du collecteur en clair, jamais une note de priorité opaque."""
    if not code:
        return 'Courriel reçu qui n’a pas encore de réponse préparée.'
    from .web440 import human
    texte = human(code)
    return texte if texte != code else code.replace('_', ' ').capitalize() + '.'


def json_ligne(r):
    return {k: r[k] for k in ('fil', 'mail_key', 'adresse', 'boite', 'de', 'objet', 'recu', 'matter', 'dossier', 'statut', 'libelle', 'precision', 'version', 'projet', 'suivi')}
