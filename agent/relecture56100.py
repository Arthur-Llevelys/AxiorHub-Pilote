"""5.6.100 (lot B · P1, R02, R03, R06, R09, V100-03) : objet commun « À relire », lecteur commun et décisions sûres.

Une seule file de relecture pour Courriels, Aujourd'hui et Produire. Le **producteur** reste la source de vérité du texte, de la version et
des droits (``manual_drafts`` pour les propositions de réponse, la messagerie pour les brouillons IMAP) ; la projection
``file56100_items`` ne garde que des références et des métadonnées (objet, dossier, date, état, révision) pour lister vite et paginer
sans trou.

- **Identité.** Une proposition reçoit un identifiant opaque stable (``p-…``) tiré du courriel et de l'instant de sa préparation : une
  relance ou une nouvelle préparation est un autre projet ; une saisie ou un choix de variante gardent le même identifiant. Un brouillon
  IMAP (``i-…``) est identifié par son Message-ID, conservé par l'enregistrement : son UID change, pas son identité. La révision d'une
  proposition est l'empreinte de tout ce que l'avocat décide (texte, destinataires, pièces, dossier, décision) ; celle d'un brouillon IMAP
  est (UIDVALIDITY, UID) : un message IMAP ne change jamais sous un même UID.
- **Liste.** Un seul tri ``updated DESC, id DESC`` (heure UTC de la préparation, en millisecondes) ; les droits, l'état, le report personnel
  et la recherche (accents et casse ignorés) sont appliqués AVANT le calcul de la page et des totaux ; curseur opaque signé, lié au compte,
  au filtre et à la recherche. Un total qui ne peut pas être compté exactement est annoncé inconnu, jamais faux. Les brouillons IMAP sont
  lus par un appel séparé : une messagerie en panne ne bloque pas la liste locale.
- **Décisions.** Enregistrer, Retenir, Écarter, Reporter : révision attendue, clé d'idempotence (même clé et mêmes paramètres = même reçu ;
  même clé et autres paramètres = 409), droits revérifiés, écriture du producteur, de la projection, de la trace et du reçu dans UNE
  transaction SQLite. « Retenir » n'envoie rien et ne dépose rien. « Déposer dans Brouillons » est une action distincte : intention durable
  écrite d'abord, dépôt IMAP hors transaction, « Déposé » seulement après relecture du brouillon chez le fournisseur ; un doute donne
  « À vérifier » et bloque un second dépôt.
- **Droits.** Garde des courriels du lot A (``acces_courriel56100``, import paresseux) ; à défaut, ``acces5697`` (dossiers réservés et
  rattachements du message lus sur le serveur). Objet absent et objet interdit : même 404 neutre ; contrôle indisponible : 503, jamais un
  accès élargi. Un brouillon IMAP hérite des restrictions de son courriel d'origine ; une origine non prouvée n'est jamais montrée à un
  compte qui a des dossiers masqués.

Aucune écriture pendant une lecture : la projection est alimentée par les producteurs (écriture), par le rattrapage du démarrage et par la
boucle des automatismes. Les schémas sont créés au démarrage (``Desk._prepare``).
"""
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from html import escape as e
import hmac
import json
import re
import secrets
import sqlite3
import time
import unicodedata
from urllib.parse import urlencode

from .common import Stop

PREFIXE = 'm56100/relecture'
CABINET = 'cabinet'
IDENT = re.compile(r'^[A-Za-z0-9_-]{1,128}$')
REVISION = re.compile(r'^[A-Za-z0-9._:-]{1,128}$')
CLE_IDEMPOTENCE = re.compile(r'^[A-Za-z0-9_-]{16,80}$')
CLE_COURRIEL = re.compile(r'^[0-9a-f]{64}$')
SECTIONS = ('message', 'pieces', 'sources')
ECRITURES = ('decision', 'enregistrer', 'deposer')
DECISIONS = ('retenir', 'ecarter', 'reporter')
SOURCES = ('tous', 'proposition', 'brouillon')
ROLES = ('administrateur', 'avocat', 'assistant')
ETATS = ('a_relire', 'retenu', 'ecarte', 'remplace', 'depose')
LIMITE, LIMITE_MAX = 30, 50
LOT = 128
TOTAL_MAX = 5000
LIMITE_IMAP = 40
MAX_CORPS = 20000            # même borne que la page du courriel (reponses5671.MAX_BODY)
MAX_CORPS_IMAP = 120_000     # drafts440.MAX_BODY
MAX_POST = 200_000
REPORT_MAX = 366 * 86400
_SECRET = secrets.token_bytes(32)   # signature des curseurs : propre au processus (un curseur d'avant un redémarrage est refusé, 422)

TYPES = {'proposition': 'Proposition', 'imap': 'Brouillon', 'gmail': 'Brouillon Gmail'}
LIBELLES_ETAT = {'a_relire': 'À relire', 'retenu': 'Retenu', 'ecarte': 'Écarté', 'remplace': 'Remplacé par une version plus récente',
                 'depose': 'Déposé dans Brouillons', 'a_verifier': 'Dépôt à vérifier dans la messagerie', 'en_cours': 'Dépôt en cours'}
MESSAGES = {'enregistre': 'Texte enregistré ; la réponse reste à relire.',
            'retenu': 'Version relue retenue. Rien n’est envoyé ni déposé.',
            'ecarte': 'Proposition écartée. Le courriel reçu et ses pièces restent dans la messagerie.',
            'reporte': 'Reportée pour vous seul.',
            'depose': 'Déposé dans Brouillons et relu dans la messagerie. Rien n’est envoyé.',
            'a_verifier': 'Dépôt incertain : vérifiez la messagerie avant toute nouvelle demande. Rien n’est envoyé.',
            'en_cours': 'Un dépôt de cette réponse est déjà en cours.'}
INCERTAINS = frozenset({'append_imap_incertain', 'verification_imap_indisponible', 'brouillon_enregistre_non_retrouve',
                        'brouillon_enregistre_non_conforme', 'brouillon_deja_depose_ou_incertain'})
PERIMES = frozenset({'version_perimee', 'brouillon_modifie_depuis_ouverture', 'uidvalidity_modifiee', 'courriel_modifie_depuis_projet',
                     'dossier_modifie_depuis_projet'})

SCHEMA = '''
CREATE TABLE IF NOT EXISTS file56100_items(
  cabinet TEXT NOT NULL, id TEXT NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('proposition','imap','gmail','document','decision')),
  matter TEXT NOT NULL DEFAULT '', mail_key TEXT NOT NULL DEFAULT '', ref_source TEXT NOT NULL, revision TEXT NOT NULL,
  titre TEXT NOT NULL, search_norm TEXT NOT NULL, updated INTEGER NOT NULL,
  etat TEXT NOT NULL DEFAULT 'a_relire' CHECK (etat IN ('a_relire','retenu','ecarte','remplace','depose')),
  PRIMARY KEY(cabinet,id));
CREATE INDEX IF NOT EXISTS file56100_page ON file56100_items(cabinet,etat,updated DESC,id DESC);
CREATE INDEX IF NOT EXISTS file56100_courriel ON file56100_items(cabinet,mail_key);
CREATE TABLE IF NOT EXISTS file56100_reports(
  cabinet TEXT NOT NULL, acteur TEXT NOT NULL, item TEXT NOT NULL, revision TEXT NOT NULL, jusqu_a INTEGER NOT NULL,
  PRIMARY KEY(cabinet,acteur,item));
CREATE TABLE IF NOT EXISTS file56100_commandes(
  cabinet TEXT NOT NULL, acteur TEXT NOT NULL, cle TEXT NOT NULL, empreinte TEXT NOT NULL, recu TEXT NOT NULL, created INTEGER NOT NULL,
  PRIMARY KEY(cabinet,acteur,cle));
CREATE TABLE IF NOT EXISTS file56100_intentions(
  cabinet TEXT NOT NULL, id TEXT NOT NULL, item TEXT NOT NULL, acteur TEXT NOT NULL, action TEXT NOT NULL, revision TEXT NOT NULL,
  cle TEXT NOT NULL, empreinte TEXT NOT NULL,
  etat TEXT NOT NULL CHECK (etat IN ('en_cours','fait','a_verifier','echec')), preuve TEXT NOT NULL DEFAULT '{}',
  created INTEGER NOT NULL, updated INTEGER NOT NULL, PRIMARY KEY(cabinet,id));
CREATE UNIQUE INDEX IF NOT EXISTS file56100_intention_active ON file56100_intentions(cabinet,item,action) WHERE etat IN ('en_cours','a_verifier');
CREATE TABLE IF NOT EXISTS file56100_historique(
  cabinet TEXT NOT NULL, item TEXT NOT NULL, revision TEXT NOT NULL, action TEXT NOT NULL, acteur TEXT NOT NULL, at INTEGER NOT NULL,
  donnees TEXT NOT NULL, PRIMARY KEY(cabinet,item,revision,action));
CREATE TABLE IF NOT EXISTS file56100_anomalies(
  cabinet TEXT NOT NULL, source TEXT NOT NULL, ref TEXT NOT NULL, motif TEXT NOT NULL, vu INTEGER NOT NULL,
  PRIMARY KEY(cabinet,source,ref));
'''


class Refus(Exception):
    """Refus d'une requête, avec son statut HTTP (404 neutre, 403 rôle, 409 version, 422 requête, 503 contrôle indisponible)."""

    def __init__(self, status, code):
        super().__init__(code)
        self.status, self.code = status, code


STATUTS = {403: '403 Forbidden', 404: '404 Not Found', 409: '409 Conflict', 422: '422 Unprocessable Entity', 503: '503 Service Unavailable'}


# ------------------------------------------------------------------------------------------------------------- outils
def canon(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), default=str)


def empreinte(value):
    return sha256(canon(value).encode()).hexdigest()


def normaliser(value):
    """Minuscules sans accents ni espaces multiples : « ÉCHÉANCE » et « echeance » se trouvent l'un l'autre."""
    return ' '.join(''.join(c for c in unicodedata.normalize('NFKD', str(value or '').casefold())
                            if not unicodedata.combining(c)).split())


def _ms(stamp):
    """Horodatage ISO → millisecondes UTC (0 si illisible)."""
    try:
        value = datetime.fromisoformat(str(stamp).replace('Z', '+00:00'))
    except (TypeError, ValueError):
        return 0
    if not value.tzinfo:
        value = value.replace(tzinfo=timezone.utc)
    return int(value.timestamp() * 1000)


def _iso(ms):
    return datetime.fromtimestamp(int(ms) / 1000, timezone.utc).isoformat() if ms else ''


def _maintenant():
    return time.time()


def ensure_schema(desk):
    """Tables additives, créées au démarrage (``Desk._prepare``), jamais pendant une lecture."""
    desk.db.executescript(SCHEMA)
    desk.db.commit()


# ------------------------------------------------------------------------------------------------------------- curseur
def curseur(valeur, portee, secret=None):
    """Curseur opaque : (updated, id) signé avec sa portée (cabinet, compte, filtre, recherche), jamais le contenu."""
    msg = canon((portee, list(valeur))).encode()
    return msg.hex() + '.' + hmac.new(secret or _SECRET, msg, sha256).hexdigest()


def decoder_curseur(jeton, portee, secret=None):
    try:
        jeton = str(jeton)
        if len(jeton) > 2000:
            raise ValueError
        part, sig = jeton.split('.')
        brut = bytes.fromhex(part)
        if not hmac.compare_digest(hmac.new(secret or _SECRET, brut, sha256).hexdigest(), sig):
            raise ValueError
        p, valeur = json.loads(brut)
        if p != portee or not isinstance(valeur, list) or len(valeur) != 2:
            raise ValueError
        quand, ident = valeur
        if type(quand) is not int or not isinstance(ident, str) or not IDENT.fullmatch(ident):
            raise ValueError
        return quand, ident
    except (ValueError, TypeError):
        raise Refus(422, 'curseur_invalide') from None


# ------------------------------------------------------------------------------------------------------------- droits
def _acteur(acteur):
    from .acces5697 import acteur_de
    try:
        a = acteur_de(acteur)
    except Stop:
        raise Refus(403, 'acteur_requis') from None
    if a.role not in ROLES:
        raise Refus(404, 'introuvable')
    return a


def _garde():
    """Garde des courriels du lot A (contrat de la feuille 5.6.100), importée au moment de l'appel ; absente : ``acces5697``."""
    try:
        from . import acces_courriel56100 as garde
    except ImportError:
        return None
    return garde if hasattr(garde, 'filtrer') else None


def _garde_brouillon(desk, a):
    """Garde passée à ``drafts440`` (lot A) : brouillon et origine contrôlés sur leurs en-têtes AVANT toute lecture du corps."""
    garde = _garde()
    if garde is None or not hasattr(garde, 'garde_brouillon'):
        return None
    return garde.garde_brouillon(desk, (a.compte, a.role), desk.c['mail'])


def _masques(desk, a):
    from .acces5697 import masques
    try:
        return masques(desk, a)
    except Stop as ex:
        if str(ex).split(':')[0] == 'role_insuffisant':
            raise Refus(404, 'introuvable') from None
        raise Refus(503, 'controle_indisponible') from None
    except (OSError, ValueError, sqlite3.Error):
        raise Refus(503, 'controle_indisponible') from None


def _stop_garde(ex):
    code = str(ex).split(':')[0]
    if code in ('introuvable', 'role_insuffisant'):
        return Refus(404, 'introuvable')
    return Refus(503, 'controle_indisponible')


def visibles(desk, acteur, lignes):
    """Identifiants des éléments que l'acteur peut voir, calculés AVANT tri, page et totaux. Contrôle en panne : 503 (jamais élargi).
    Un brouillon IMAP sans origine prouvée (``mail_key`` vide) n'est montré qu'à un compte sans dossier masqué."""
    a = _acteur(acteur)
    lignes = list(lignes)
    if not lignes:
        return set()
    caches = _masques(desk, a)
    if not caches:
        return {x['id'] for x in lignes}
    lignes = [x for x in lignes if x.get('kind') != 'imap' or CLE_COURRIEL.fullmatch(str(x.get('mail_key') or ''))]
    garde = _garde()
    if garde is not None:
        try:   # garde du lot A : rattachements du message et du projet relus sur le serveur, dossier déclaré compté en plus
            gardees = garde.filtrer(desk, (a.compte, a.role), [{'id': x['id'], 'mail_key': x.get('mail_key') or '', 'matter': x.get('matter') or ''}
                                                               for x in lignes])
        except Stop as ex:
            raise _stop_garde(ex) from None
        return {r['id'] for r in gardees or () if isinstance(r, dict)}
    from .acces5697 import visible
    from .messages5697 import dossiers_par_cle
    gardes = [x for x in lignes if str(x.get('matter') or '') not in caches]
    cles = [x['mail_key'] for x in gardes if CLE_COURRIEL.fullmatch(str(x.get('mail_key') or ''))]
    try:
        assoc = dossiers_par_cle(desk, cles) if cles else {}
    except (Stop, OSError, sqlite3.Error):
        raise Refus(503, 'controle_indisponible') from None
    out = set()
    for x in gardes:
        cle = str(x.get('mail_key') or '')
        if CLE_COURRIEL.fullmatch(cle):
            if visible(assoc.get(cle, ()), caches):
                out.add(x['id'])
        # brouillon sans origine prouvée : jamais montré à un compte qui a des dossiers masqués
    return out


def exiger(desk, acteur, ligne):
    """Refus neutre (404) si l'acteur ne peut pas voir cet élément."""
    if ligne is None or ligne['id'] not in visibles(desk, acteur, [ligne]):
        raise Refus(404, 'introuvable')


def peut_deposer(desk, acteur):
    """Droit « déposer » du compte (membres5650) : enregistrer, retenir, écarter et déposer. Reporter reste ouvert à tous les rôles."""
    a = _acteur(acteur)
    if a.role == 'administrateur':
        return True
    from . import membres5650
    try:
        return bool(membres5650.effective_rights(desk.c['state_dir'], a.compte, a.role).get('deposer'))
    except Stop:
        return False


def autoriser_route(role, method, name):
    """Liste fermée des routes et des rôles (``standalone_auth.allowed`` et ``web440.api``) ; le droit sur l'objet est vérifié ensuite."""
    if role not in ROLES:
        return False
    path = str(name or '').split('?', 1)[0]
    if path == PREFIXE:
        return method in ('GET', 'HEAD')
    if not path.startswith(PREFIXE + '/'):
        return False
    parts = path[len(PREFIXE) + 1:].split('/')
    if not IDENT.fullmatch(parts[0]) or len(parts) > 2:
        return False
    if method in ('GET', 'HEAD'):
        return len(parts) == 1 or parts[1] in SECTIONS
    if method == 'POST':
        return len(parts) == 2 and parts[1] in ECRITURES
    return False


# ------------------------------------------------------------------------------------------------------------- producteurs
def id_proposition(cle, data):
    cree = str((data or {}).get('created_at') or '')
    return 'p-' + sha256(('proposition|%s|%s' % (cle, cree)).encode()).hexdigest()[:32]


def revision_proposition(data):
    """Empreinte de tout ce que l'avocat relit et décide : texte, destinataires, pièces, dossier, contrôle et décision."""
    return 'r' + empreinte(data)[:24]


def revision_courante(desk, cle):
    """Révision actuelle de la proposition d'un courriel (formulaires historiques de la page du courriel) ; '' sans proposition lisible."""
    row = desk.db.execute('SELECT data FROM manual_drafts WHERE mail_key=?', (str(cle or ''),)).fetchone()
    try:
        data = json.loads(row[0]) if row else None
    except (TypeError, ValueError):
        return ''
    return revision_proposition(data) if isinstance(data, dict) else ''


def _rapport(desk, cle):
    from .desk import report_for
    try:
        return report_for(desk.c, cle) or {}
    except (Stop, OSError, ValueError):
        return {}


def _libelles(desk):
    try:
        from .common import load_matters, matter_display
        return {str(m['id']): matter_display(m) for m in load_matters(desk.c, unfiltered=True)}
    except Exception:   # noqa: BLE001 — registre illisible : les identifiants restent affichés
        return {}


def _anomalie(desk, source, ref, motif):
    desk.db.execute('INSERT INTO file56100_anomalies VALUES(?,?,?,?,?) ON CONFLICT(cabinet,source,ref) DO UPDATE SET motif=excluded.motif,vu=excluded.vu',
                    (CABINET, source, str(ref)[:200], str(motif)[:200], int(_maintenant())))


def _ligne_proposition(desk, cle, data, maj, libelles):
    result = data.get('result') if isinstance(data.get('result'), dict) else {}
    if not str(result.get('body') or '').strip():
        return None
    decision = data.get('decision56100') if isinstance(data.get('decision56100'), dict) else {}
    if decision.get('etat') == 'retenu':
        etat = 'retenu'
    elif result.get('requires_decision'):
        etat = 'a_relire'
    else:
        return None                     # prêt à déposer sans décision : hors de la file de relecture
    rapport = _rapport(desk, cle)
    sujet = str(rapport.get('subject') or 'Courriel')[:200]
    mid = str(data.get('matter') or '')
    dest = ', '.join(str(x) for x in (data.get('recipients') or []) if x)
    recherche = normaliser(' '.join((sujet, libelles.get(mid, mid), dest, str(rapport.get('sender') or ''))))[:1500]
    return {'id': id_proposition(cle, data), 'kind': 'proposition', 'matter': mid, 'mail_key': cle, 'ref_source': 'manual_drafts:' + cle,
            'revision': revision_proposition(data), 'titre': sujet, 'search_norm': recherche,
            'updated': _ms(data.get('created_at')) or _ms(maj), 'etat': etat}


def _upsert(desk, ligne):
    desk.db.execute('INSERT INTO file56100_items(cabinet,id,kind,matter,mail_key,ref_source,revision,titre,search_norm,updated,etat) '
                    'VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(cabinet,id) DO UPDATE SET kind=excluded.kind,matter=excluded.matter,'
                    'mail_key=excluded.mail_key,ref_source=excluded.ref_source,revision=excluded.revision,titre=excluded.titre,'
                    'search_norm=excluded.search_norm,updated=excluded.updated,etat=excluded.etat',
                    (CABINET, ligne['id'], ligne['kind'], ligne['matter'], ligne['mail_key'], ligne['ref_source'], ligne['revision'],
                     ligne['titre'], ligne['search_norm'], int(ligne['updated']), ligne['etat']))


def _etat_disparu(desk, cle):
    """Proposition retirée du producteur sans passer par le lecteur : déposée (état du courriel) ou remplacée (Gmail, relance…)."""
    try:
        from .state import State
        row = State(desk.c['state_dir']).get(cle)
    except Exception:   # noqa: BLE001 — état illisible : jamais « déposé » sans preuve
        row = None
    return 'depose' if row and row[0] == 'drafted' else 'remplace'


def projeter_cle(desk, cle, commit=True, libelles=None):
    """Met la projection à jour depuis le producteur pour un courriel (appelé après chaque écriture de ``manual_drafts``)."""
    cle = str(cle or '')
    if not CLE_COURRIEL.fullmatch(cle):
        return None
    row = desk.db.execute('SELECT data,updated FROM manual_drafts WHERE mail_key=?', (cle,)).fetchone()
    actuel = ''
    if row:
        try:
            data = json.loads(row[0])
        except (TypeError, ValueError):
            data = None
        if not isinstance(data, dict):
            _anomalie(desk, 'manual_drafts', cle, 'json_illisible')    # isolée et comptée, jamais lue comme « aucun résultat »
        else:
            ligne = _ligne_proposition(desk, cle, data, row[1], libelles if libelles is not None else _libelles(desk))
            if ligne:
                _upsert(desk, ligne)
                actuel = ligne['id']
    fin = 'remplace' if row else _etat_disparu(desk, cle)
    desk.db.execute("UPDATE file56100_items SET etat=? WHERE cabinet=? AND kind='proposition' AND mail_key=? AND id<>? AND etat IN ('a_relire','retenu')",
                    (fin, CABINET, cle, actuel))
    if commit:
        desk.db.commit()
    return actuel or None


def projeter_apres_ecriture(desk, cle):
    """Point d'appel des producteurs : ne casse jamais l'écriture qui vient de réussir."""
    try:
        projeter_cle(desk, cle)
    except (sqlite3.Error, OSError, ValueError):
        try:
            desk.db.rollback()
        except sqlite3.Error:
            pass


def rattraper(desk, limite=500, noter=True):
    """Rattrapage relançable et borné : propositions écrites depuis le dernier passage, puis propositions disparues du producteur."""
    fil = desk.settings('relecture56100:filigrane', None)
    fil = fil if isinstance(fil, list) and len(fil) == 2 else ['', '']
    libelles = _libelles(desk)
    rows = desk.db.execute("SELECT mail_key,COALESCE(updated,'') FROM manual_drafts WHERE (COALESCE(updated,''),mail_key)>(?,?) "
                           "ORDER BY COALESCE(updated,''),mail_key LIMIT ?", (str(fil[0]), str(fil[1]), int(limite))).fetchall()
    for r in rows:
        projeter_cle(desk, r[0], commit=False, libelles=libelles)
    disparus = desk.db.execute("SELECT DISTINCT i.mail_key FROM file56100_items i WHERE i.cabinet=? AND i.kind='proposition' "
                               "AND i.etat IN ('a_relire','retenu') AND NOT EXISTS (SELECT 1 FROM manual_drafts m WHERE m.mail_key=i.mail_key) LIMIT ?",
                               (CABINET, int(limite))).fetchall()
    for r in disparus:
        projeter_cle(desk, r[0], commit=False, libelles=libelles)
    desk.db.commit()
    if rows:
        desk.setting('relecture56100:filigrane', [rows[-1][1], rows[-1][0]])
    termine = len(rows) < limite and len(disparus) < limite
    if termine and not desk.settings('relecture56100:migre', False):
        desk.setting('relecture56100:migre', True)
    if noter or rows or disparus:   # démarrage (noter=False) d'une base inchangée : aucune écriture (garde 5.6.44)
        desk.setting('relecture56100:synchronise', datetime.now(timezone.utc).isoformat())
    return {'projetes': len(rows), 'disparus': len(disparus), 'termine': termine}


def anomalies(desk):
    """Maintenance : nombre d'entrées illisibles isolées (jamais confondues avec « aucun résultat »)."""
    return desk.db.execute('SELECT COUNT(*) FROM file56100_anomalies WHERE cabinet=?', (CABINET,)).fetchone()[0]


# ------------------------------------------------------------------------------------------------------------- liste
def _page_locale(desk, a, kinds, q, limite, apres, maintenant):
    """Scan ordonné par lots : droits par lot AVANT la page, arrêt à page + 1 éléments visibles (curseur sans trou)."""
    from .acces5697 import Perimetre
    base = ("SELECT i.id,i.kind,i.matter,i.mail_key,i.titre,i.revision,i.updated,i.etat FROM file56100_items i WHERE i.cabinet=? AND i.etat='a_relire' "
            "AND NOT EXISTS (SELECT 1 FROM file56100_reports r WHERE r.cabinet=i.cabinet AND r.item=i.id AND r.revision=i.revision "
            "AND r.acteur=? AND r.jusqu_a>?)")
    params = [CABINET, a.compte, int(maintenant)]
    base += ' AND i.kind IN (%s)' % ','.join('?' * len(kinds))
    params += list(kinds)
    frag, extra = Perimetre(a, _masques(desk, a)).sql('i.matter')   # dossiers masqués exclus dans la requête, avant le scan
    base += ' AND ' + frag
    params += list(extra)
    if q:
        base += ' AND instr(i.search_norm,?)>0'
        params.append(q)
    items = []
    while True:
        pred = ' AND (i.updated,i.id)<(?,?)' if apres is not None else ''
        rows = [dict(r) for r in desk.db.execute(base + pred + ' ORDER BY i.updated DESC,i.id DESC LIMIT %d' % LOT,
                                                 params + list(apres or ())).fetchall()]
        if not rows:
            return items, None
        ids = visibles(desk, a, rows)
        for row in rows:
            if row['id'] in ids:
                items.append(row)
                if len(items) > limite:
                    dernier = items[limite - 1]
                    return items[:limite], (dernier['updated'], dernier['id'])
        apres = (rows[-1]['updated'], rows[-1]['id'])


def _total(desk, a, kinds, q, maintenant):
    """Total exact avec les mêmes droits et filtres ; au-delà de ``TOTAL_MAX`` éléments examinés : inconnu (None)."""
    vus = 0
    total, apres = 0, None
    while True:
        page, suite = _page_locale(desk, a, kinds, q, LOT, apres, maintenant)
        total += len(page)
        vus += len(page)
        if suite is None:
            return total
        if vus >= TOTAL_MAX:
            return None
        apres = suite


def _date_locale(desk, ms, owner='cabinet'):
    if not ms:
        return ''
    try:
        from zoneinfo import ZoneInfo
        nom = _fuseau(desk, owner)
        local = datetime.fromtimestamp(ms / 1000, timezone.utc).astimezone(ZoneInfo(nom))
        aujourdhui = datetime.now(ZoneInfo(nom)).date()
    except Exception:   # noqa: BLE001
        local = datetime.fromtimestamp(ms / 1000, timezone.utc)
        aujourdhui = datetime.now(timezone.utc).date()
    if local.date() == aujourdhui:
        return local.strftime('%H:%M')
    if local.date() == aujourdhui - timedelta(days=1):
        return 'hier ' + local.strftime('%H:%M')
    return local.strftime('%d/%m %H:%M')


def _fuseau(desk, owner='cabinet'):
    """Fuseau du compte (profil de la 5.6.68), à défaut celui du cabinet ; jamais l'heure du serveur."""
    try:
        from .settings568 import profile
        nom = str(profile(desk, owner).get('timezone') or '')
        if nom:
            from zoneinfo import ZoneInfo
            ZoneInfo(nom)
            return nom
    except Exception:   # noqa: BLE001
        pass
    return str((desk.c.get('proactive') or {}).get('timezone') or 'Europe/Paris')


def _sortie_ligne(desk, prefix, ligne, libelles, owner):
    mid = str(ligne.get('matter') or '')
    return {'id': ligne['id'], 'type': ligne['kind'], 'type_libelle': TYPES.get(ligne['kind'], ligne['kind']),
            'statut': ligne.get('etat') or 'a_relire', 'statut_libelle': LIBELLES_ETAT.get(ligne.get('etat') or 'a_relire', ''),
            'objet': ligne.get('titre') or '(sans objet)', 'date': _iso(ligne.get('updated') or 0), 'maj': int(ligne.get('updated') or 0),
            'date_affichee': _date_locale(desk, ligne.get('updated') or 0, owner),
            'dossier': libelles.get(mid, mid) if mid else 'Sans dossier', 'a': ligne.get('a') or '',
            'agent': bool(ligne.get('agent', ligne['kind'] == 'proposition')),
            'href': prefix + '/courriels?' + urlencode({'item': ligne['id']})}


def _etat_local(desk):
    migre = bool(desk.settings('relecture56100:migre', False))
    return {'etat': 'a_jour' if migre else 'synchronisation_en_cours', 'synchronise_le': str(desk.settings('relecture56100:synchronise', '') or ''),
            'anomalies': anomalies(desk)}


def liste(desk, acteur, args, prefix='', maintenant=None):
    """``GET …/relecture`` : lignes autorisées, curseur suivant, total (exact ou inconnu) et état des sources. Aucune écriture."""
    a = _acteur(acteur)
    source = str(args.get('source') or 'tous')
    if source not in SOURCES:
        raise Refus(422, 'source_invalide')
    q = normaliser(str(args.get('q') or '')[:120])
    try:
        limite = int(args.get('limite') or LIMITE)
    except (TypeError, ValueError):
        raise Refus(422, 'limite_invalide') from None
    if not 1 <= limite <= LIMITE_MAX:
        raise Refus(422, 'limite_invalide')
    maintenant = int(_maintenant() if maintenant is None else maintenant)
    libelles = _libelles(desk)
    if source == 'brouillon':
        lignes, etat = brouillons(desk, a, q, maintenant)
        return {'lignes': [_sortie_ligne(desk, prefix, x, libelles, a.compte) for x in lignes], 'curseur': None, 'total': len(lignes),
                'total_exact': bool(etat.get('complet')), 'source': source, 'q': q, 'sources': {'imap': etat}}
    portee = [CABINET, a.compte, source, q]
    apres = decoder_curseur(args['curseur'], portee) if args.get('curseur') else None
    kinds = ('proposition',)
    page, suite = _page_locale(desk, a, kinds, q, limite, apres, maintenant)
    total = _total(desk, a, kinds, q, maintenant) if apres is None else None
    return {'lignes': [_sortie_ligne(desk, prefix, x, libelles, a.compte) for x in page],
            'curseur': curseur(suite, portee) if suite else None, 'total': total, 'total_exact': total is not None,
            'source': source, 'q': q,
            'sources': {'local': _etat_local(desk), 'imap': {'etat': 'a_charger' if source == 'tous' else 'hors_filtre'}}}


# ------------------------------------------------------------------------------------------------------------- brouillons IMAP
def _compte_mail(desk):
    cfg = desk.c.get('mail') or {}
    return '%s@%s' % (cfg.get('username') or '', cfg.get('host') or '')


def id_imap(desk, message_id, validity, uid, dossier=None):
    """Identifiant stable d'un brouillon : son Message-ID (conservé par l'enregistrement) ; à défaut (UIDVALIDITY, UID)."""
    dossier = dossier if dossier is not None else (desk.c.get('mail') or {}).get('drafts', '')
    natif = ('mid:' + str(message_id)) if message_id else 'uid:%s:%s' % (validity, uid)
    return 'i-' + sha256(('imap|%s|%s|%s' % (_compte_mail(desk), dossier, natif)).encode()).hexdigest()[:32]


def revision_imap(validity, uid):
    return 'v%su%s' % (validity, uid)


def _lire_revision_imap(revision):
    found = re.fullmatch(r'v(\d{1,20})u(\d{1,12})', str(revision or ''))
    if not found:
        raise Refus(409, 'version_perimee')
    return found.group(1), found.group(2)


def _origine(desk, item):
    """Courriel d'origine prouvé d'un brouillon déposé par l'agent (registre du dépôt) ; '' si aucune preuve."""
    from .reponses5671 import mail_of_draft
    cle = mail_of_draft(desk, str(item.get('agent_key') or ''))
    return cle if CLE_COURRIEL.fullmatch(str(cle or '')) else ''


def _ligne_imap(desk, item, validity, dossier):
    origine = _origine(desk, item)
    matter = ''
    if origine:
        matter = str(_rapport(desk, origine).get('matter') or '')
    return {'id': id_imap(desk, item.get('message_id'), validity, item['uid'], dossier), 'kind': 'imap', 'matter': matter,
            'mail_key': origine, 'titre': str(item.get('subject') or '(sans objet)')[:200], 'revision': revision_imap(validity, item['uid']),
            'updated': _ms(item.get('date')), 'etat': 'a_relire', 'a': str(item.get('to') or '')[:200], 'agent': bool(item.get('agent')),
            'uid': str(item['uid']), 'uidvalidity': str(validity), 'message_id': str(item.get('message_id') or ''),
            'in_reply_to': str(item.get('in_reply_to') or ''), 'agent_key': str(item.get('agent_key') or ''), 'dossier_imap': dossier,
            'search_norm': normaliser(' '.join((str(item.get('subject') or ''), str(item.get('to') or ''))))}


def _lister_imap(desk, a, limite=LIMITE_IMAP):
    """Brouillons de la messagerie autorisés pour l'acteur : chaque brouillon est résolu par la garde du lot A (origine prouvée sur les
    en-têtes, lien incertain masqué à un compte cloisonné) AVANT d'être transformé en ligne ; sans garde : ``visibles``."""
    from . import drafts440
    listing = drafts440.list_drafts(desk.c['mail'], limite)
    complet = len(listing['items']) < limite
    garde = _garde()
    if garde is not None and hasattr(garde, 'filtrer_brouillons'):
        try:
            listing = garde.filtrer_brouillons(desk, (a.compte, a.role), desk.c['mail'], listing)
        except Stop as ex:
            raise _stop_garde(ex) from None
    dossier = listing.get('folder') or desk.c['mail'].get('drafts', '')
    lignes = [_ligne_imap(desk, it, listing['uidvalidity'], dossier) for it in listing['items']]
    if garde is None:
        autorises = visibles(desk, a, lignes)
        lignes = [x for x in lignes if x['id'] in autorises]
    return lignes, complet


def brouillons(desk, acteur, q='', maintenant=None):
    """Brouillons de la messagerie (appel séparé de la liste locale) : décisions locales et reports de l'acteur appliqués, droits avant tout."""
    a = _acteur(acteur)
    maintenant = int(_maintenant() if maintenant is None else maintenant)
    lu = datetime.now(timezone.utc).isoformat()
    try:
        lignes, complet = _lister_imap(desk, a)
    except Stop as ex:
        return [], {'etat': 'indisponible', 'raison': _humain(str(ex).split(':')[0]), 'lu_le': lu}
    except Exception:   # noqa: BLE001 — messagerie injoignable : la liste locale reste utilisable
        return [], {'etat': 'indisponible', 'raison': 'La messagerie ne répond pas. Les propositions locales restent disponibles.', 'lu_le': lu}
    if lignes:
        marques = ','.join('?' * len(lignes))
        ids = [x['id'] for x in lignes]
        decides = {(r[0], r[1]) for r in desk.db.execute(
            "SELECT id,revision FROM file56100_items WHERE cabinet=? AND kind='imap' AND etat<>'a_relire' AND id IN (%s)" % marques, [CABINET] + ids)}
        reportes = {(r[0], r[1]) for r in desk.db.execute(
            'SELECT item,revision FROM file56100_reports WHERE cabinet=? AND acteur=? AND jusqu_a>? AND item IN (%s)' % marques,
            [CABINET, a.compte, maintenant] + ids)}
        lignes = [x for x in lignes if (x['id'], x['revision']) not in decides and (x['id'], x['revision']) not in reportes]
    if q:
        lignes = [x for x in lignes if q in x['search_norm']]
    lignes.sort(key=lambda x: (x['updated'], x['id']), reverse=True)
    return lignes, {'etat': 'a_jour', 'lu_le': lu, 'complet': complet}


def _trouver_imap(desk, a, ident):
    """Brouillon désigné, cherché parmi ceux que l'acteur peut voir (absent ou interdit : None, puis 404 neutre)."""
    try:
        lignes, _ = _lister_imap(desk, a, 100)
    except Stop as ex:
        raise Refus(503, 'source_indisponible:' + str(ex).split(':')[0]) from None
    except Exception:   # noqa: BLE001
        raise Refus(503, 'source_indisponible') from None
    for x in lignes:
        if x['id'] == ident:
            return x
    return None


def _refus_imap(ex):
    code = str(ex).split(':')[0]
    if code == 'introuvable':
        return Refus(404, 'introuvable')
    if code == 'controle_indisponible':
        return Refus(503, 'controle_indisponible')
    if code in PERIMES:
        return Refus(409, 'version_perimee')
    return Refus(503, 'source_indisponible')


def _humain(code):
    try:
        from .web440 import human
        return human(code)
    except Exception:   # noqa: BLE001
        return code.replace('_', ' ')


# ------------------------------------------------------------------------------------------------------------- détail
def _ligne_projetee(desk, ident):
    row = desk.db.execute('SELECT * FROM file56100_items WHERE cabinet=? AND id=?', (CABINET, ident)).fetchone()
    return dict(row) if row else None


def _producteur(desk, cle):
    row = desk.db.execute('SELECT data,updated FROM manual_drafts WHERE mail_key=?', (cle,)).fetchone()
    if not row:
        return None
    try:
        data = json.loads(row[0])
    except (TypeError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _actions(desk, a, kind, etat):
    peut = peut_deposer(desk, a)
    out = []
    if etat == 'a_relire':
        if peut:
            out += ['enregistrer', 'retenir', 'ecarter']
        out.append('reporter')
        if peut and kind == 'proposition':
            out.append('deposer')
    elif etat == 'retenu' and kind == 'proposition' and peut:
        out.append('deposer')
    return out


def _detail_proposition(desk, a, ligne, prefix):
    exiger(desk, a, ligne)
    cle = ligne['mail_key']
    data = _producteur(desk, cle)
    courant = data is not None and id_proposition(cle, data) == ligne['id']
    libelles = _libelles(desk)
    rapport = _rapport(desk, cle)
    if courant:
        mid = str(data.get('matter') or '')
        if mid and mid != ligne['matter']:     # dossier changé depuis la projection : le producteur fait foi
            exiger(desk, a, {**ligne, 'matter': mid})
        result = data.get('result') if isinstance(data.get('result'), dict) else {}
        decision = data.get('decision56100') if isinstance(data.get('decision56100'), dict) else {}
        etat = 'retenu' if decision.get('etat') == 'retenu' else ('a_relire' if result.get('requires_decision') else 'retenu')
        corps, revision = str(result.get('body') or ''), revision_proposition(data)
        destinataires = [str(x) for x in (data.get('recipients') or []) if x]
        motif = ' · '.join(str(x) for x in (result.get('limits') or []) if str(x).strip())[:600]
        remplacant = ''
    else:
        mid = ligne['matter']
        etat = ligne['etat'] if ligne['etat'] != 'a_relire' else 'remplace'
        trace = desk.db.execute('SELECT donnees FROM file56100_historique WHERE cabinet=? AND item=? ORDER BY at DESC LIMIT 1',
                                (CABINET, ligne['id'])).fetchone()
        ancien = json.loads(trace[0]) if trace else {}
        corps, revision = str(ancien.get('corps') or ''), ligne['revision']
        destinataires = [str(x) for x in ancien.get('destinataires') or []]
        motif = ''
        remplacant = id_proposition(cle, data) if data is not None else ''
        if remplacant and remplacant not in visibles(desk, a, [{'id': remplacant, 'kind': 'proposition', 'matter': str(data.get('matter') or ''),
                                                               'mail_key': cle}]):
            remplacant = ''
    from urllib.parse import quote
    return {'id': ligne['id'], 'type': 'proposition', 'type_libelle': TYPES['proposition'], 'revision': revision, 'etat': etat,
            'etat_libelle': LIBELLES_ETAT.get(etat, etat), 'sujet': ligne['titre'], 'corps': corps,
            'dossier': {'id': mid, 'libelle': libelles.get(mid, mid) if mid else 'Sans dossier'},
            'destinataires': {'a': destinataires[:1], 'cc': destinataires[1:]}, 'expediteur': str(rapport.get('sender') or ''),
            'recu_le': str(rapport.get('received_at') or ''), 'motif': motif, 'actions': _actions(desk, a, 'proposition', etat),
            'sections': list(SECTIONS), 'editable': etat == 'a_relire', 'remplace_par': remplacant,
            'liens': {'pleine_page': prefix + '/mail?key=' + quote(cle, safe='')}}


def _detail_imap(desk, a, ident, prefix):
    from . import drafts440
    trouve = _trouver_imap(desk, a, ident)
    if trouve is None:
        projete = _ligne_projetee(desk, ident)
        if projete is None:
            raise Refus(404, 'introuvable')
        exiger(desk, a, projete)
        return {'id': ident, 'type': 'imap', 'type_libelle': TYPES['imap'], 'revision': projete['revision'], 'etat': 'remplace',
                'etat_libelle': 'Brouillon absent de la messagerie (envoyé, supprimé ou déplacé)', 'sujet': projete['titre'], 'corps': '',
                'dossier': {'id': projete['matter'], 'libelle': ''}, 'destinataires': {'a': [], 'cc': []}, 'actions': [], 'sections': [],
                'editable': False, 'liens': {}}
    try:                                        # garde du lot A dans la session IMAP, AVANT toute lecture du corps
        d = drafts440.get_draft(desk.c['mail'], trouve['uid'], trouve['uidvalidity'], garde=_garde_brouillon(desk, a))
    except Stop as ex:
        raise _refus_imap(ex) from None
    projete = _ligne_projetee(desk, ident)
    revision = revision_imap(d['uidvalidity'], d['uid'])
    etat = projete['etat'] if projete and projete['revision'] == revision and projete['etat'] != 'a_relire' else 'a_relire'
    libelles = _libelles(desk)
    links = {}
    try:
        from .workstation import external_links
        lien = external_links(desk).get('roundcube_drafts') or external_links(desk).get('roundcube') or ''
        if lien:
            links['messagerie'] = lien
    except Exception:   # noqa: BLE001
        pass
    if trouve.get('mail_key'):
        from urllib.parse import quote
        links['pleine_page'] = prefix + '/mail?key=' + quote(trouve['mail_key'], safe='')
    return {'id': ident, 'type': 'imap', 'type_libelle': TYPES['imap'], 'revision': revision, 'etat': etat,
            'etat_libelle': LIBELLES_ETAT.get(etat, etat), 'sujet': d.get('subject') or '(sans objet)', 'corps': d.get('body') or '',
            'dossier': {'id': trouve['matter'], 'libelle': libelles.get(trouve['matter'], trouve['matter']) if trouve['matter'] else 'Sans dossier'},
            'destinataires': {'a': [x.strip() for x in str(d.get('to') or '').split(',') if x.strip()],
                              'cc': [x.strip() for x in str(d.get('cc') or '').split(',') if x.strip()]},
            'expediteur': d.get('from') or '', 'motif': '', 'actions': _actions(desk, a, 'imap', etat) if d.get('editable', True) else
            [x for x in _actions(desk, a, 'imap', etat) if x != 'enregistrer'],
            'sections': list(SECTIONS), 'editable': bool(d.get('editable', True)) and etat == 'a_relire',
            'mise_en_forme_perdue': bool(d.get('lossy')), 'pieces': len(d.get('attachments') or []), 'liens': links}


def _kind_de(ident):
    if ident.startswith('p-'):
        return 'proposition'
    if ident.startswith('i-'):
        return 'imap'
    return ''


def detail(desk, acteur, ident, prefix=''):
    """``GET …/relecture/{id}`` : id, révision, dossier, sujet, corps, destinataires, état et actions permises. Aucune écriture."""
    a = _acteur(acteur)
    if not IDENT.fullmatch(str(ident or '')):
        raise Refus(404, 'introuvable')
    kind = _kind_de(ident)
    if kind == 'proposition':
        ligne = _ligne_projetee(desk, ident)
        if ligne is None or ligne['kind'] != 'proposition':
            raise Refus(404, 'introuvable')
        return _detail_proposition(desk, a, ligne, prefix)
    if kind == 'imap':
        return _detail_imap(desk, a, ident, prefix)
    raise Refus(404, 'introuvable')


def section(desk, acteur, ident, revision, sous):
    """Sous-routes Message reçu, Pièces, Sources : version affichée exigée, accès revalidé, pièces sans leur texte."""
    a = _acteur(acteur)
    if sous not in SECTIONS or not IDENT.fullmatch(str(ident or '')):
        raise Refus(404, 'introuvable')
    if not REVISION.fullmatch(str(revision or '')):
        raise Refus(422, 'version_requise')
    kind = _kind_de(ident)
    if kind == 'proposition':
        ligne = _ligne_projetee(desk, ident)
        if ligne is None:
            raise Refus(404, 'introuvable')
        exiger(desk, a, ligne)
        data = _producteur(desk, ligne['mail_key'])
        if data is None or id_proposition(ligne['mail_key'], data) != ident or revision_proposition(data) != revision:
            raise Refus(409, 'version_perimee')
        if sous == 'sources':
            result = data.get('result') if isinstance(data.get('result'), dict) else {}
            sources = [{'id': str(s.get('id') or '')[:80], 'titre': str(s.get('title') or s.get('titre') or s.get('name') or s.get('id') or '')[:200],
                        'type': str(s.get('type') or s.get('kind') or '')[:40]} for s in (data.get('sources') or []) if isinstance(s, dict)][:60]
            return {'id': ident, 'revision': revision, 'section': 'sources', 'sources': sources,
                    'limites': [str(x)[:400] for x in (result.get('limits') or [])][:20]}
        return _section_courriel(desk, ligne['mail_key'], ident, revision, sous)
    if kind == 'imap':
        trouve = _trouver_imap(desk, a, ident)
        if trouve is None:
            raise Refus(404, 'introuvable')
        if trouve['revision'] != revision:
            raise Refus(409, 'version_perimee')
        if sous == 'message' and trouve.get('mail_key'):
            return _section_courriel(desk, trouve['mail_key'], ident, revision, 'message')
        if sous == 'pieces':
            from . import drafts440
            try:
                d = drafts440.get_draft(desk.c['mail'], trouve['uid'], trouve['uidvalidity'], garde=_garde_brouillon(desk, a))
            except Stop as ex:
                if str(ex).split(':')[0] in ('introuvable', 'controle_indisponible'):
                    raise _refus_imap(ex) from None
                return {'id': ident, 'revision': revision, 'section': sous, 'indisponible': True, 'raison': 'Messagerie indisponible.'}
            return {'id': ident, 'revision': revision, 'section': sous,
                    'pieces': [{'nom': str(p.get('filename') or 'pièce')[:200], 'type': str(p.get('type') or '')[:80], 'taille': int(p.get('size') or 0)}
                               for p in d.get('attachments') or []]}
        return {'id': ident, 'revision': revision, 'section': sous, 'indisponible': True,
                'raison': 'Aucun courriel d’origine prouvé pour ce brouillon.' if sous == 'message' else 'Aucune source enregistrée pour ce brouillon.'}
    raise Refus(404, 'introuvable')


def _section_courriel(desk, cle, ident, revision, sous):
    """Courriel d'origine relu dans la messagerie après le contrôle d'accès ; un UID réutilisé (autre message) n'est jamais montré."""
    rapport = _rapport(desk, cle)
    tete = {'id': ident, 'revision': revision, 'section': sous, 'objet': str(rapport.get('subject') or ''),
            'expediteur': str(rapport.get('sender') or ''), 'recu_le': str(rapport.get('received_at') or '')}
    uid, boite = str(rapport.get('source_uid') or ''), str(rapport.get('source_mailbox') or '')
    if not uid or not boite:
        return {**tete, 'indisponible': True, 'raison': 'Le courriel d’origine n’est plus localisé dans la messagerie.'}
    from .mailbox import Mailbox
    try:
        box = Mailbox(desk.c['mail'])
        try:
            mail = box.fetch(boite, uid)
        finally:
            box.close()
    except Exception:   # noqa: BLE001 — messagerie en panne : le projet local reste lisible
        return {**tete, 'indisponible': True, 'raison': 'La messagerie ne répond pas. Réessayez.'}
    if mail.key(_compte_mail(desk)) != cle:
        return {**tete, 'indisponible': True, 'raison': 'Le courriel d’origine a changé de place dans la messagerie.'}
    if sous == 'pieces':
        pieces = []
        for part in mail.msg.iter_attachments():
            pieces.append({'nom': str(part.get_filename() or 'pièce')[:200], 'type': part.get_content_type(),
                           'taille': len(part.get_payload(decode=True) or b'')})
        return {**tete, 'pieces': pieces[:50]}
    texte = str(mail.text or '')
    return {**tete, 'texte': texte[:40000], 'tronque': len(texte) > 40000}


# ------------------------------------------------------------------------------------------------------------- commandes
def _valider(sous, data, maintenant):
    action = 'enregistrer' if sous == 'enregistrer' else ('deposer' if sous == 'deposer' else data.get('action'))
    if sous == 'decision' and action not in DECISIONS:
        raise Refus(422, 'commande_invalide')
    revision = data.get('revision')
    if not isinstance(revision, str) or not REVISION.fullmatch(revision):
        raise Refus(422, 'version_requise')
    cle = data.get('cle')
    if not isinstance(cle, str) or not CLE_IDEMPOTENCE.fullmatch(cle):
        raise Refus(422, 'cle_invalide')
    corps = data.get('corps')
    if action in ('retenir', 'enregistrer') or (action == 'deposer' and corps is not None):
        if not isinstance(corps, str) or not corps.strip() or '\x00' in corps or len(corps) > MAX_CORPS_IMAP:
            raise Refus(422, 'texte_invalide')
        corps = corps.replace('\r\n', '\n').replace('\r', '\n')
    elif corps is not None:
        raise Refus(422, 'saisie_non_enregistree')
    jusqu_a = data.get('jusqu_a')
    if action == 'reporter':
        if type(jusqu_a) is not int or not maintenant < jusqu_a <= maintenant + REPORT_MAX:
            raise Refus(422, 'report_invalide')
    elif jusqu_a is not None:
        raise Refus(422, 'report_invalide')
    return action, revision, cle, corps, jusqu_a


def _recu_anterieur(desk, a, cle, sig):
    prior = desk.db.execute('SELECT empreinte,recu FROM file56100_commandes WHERE cabinet=? AND acteur=? AND cle=?',
                            (CABINET, a.compte, cle)).fetchone()
    if not prior:
        return None
    if prior['empreinte'] != sig:
        raise Refus(409, 'cle_reutilisee')
    return json.loads(prior['recu'])


def _noter_recu(desk, a, cle, sig, recu, maintenant):
    desk.db.execute('INSERT INTO file56100_commandes VALUES(?,?,?,?,?,?)', (CABINET, a.compte, cle, sig, canon(recu), int(maintenant)))


def _local_jusqu_a(desk, owner, jusqu_a):
    try:
        from zoneinfo import ZoneInfo
        return datetime.fromtimestamp(jusqu_a, timezone.utc).astimezone(ZoneInfo(_fuseau(desk, owner))).strftime('%d/%m/%Y à %H:%M')
    except Exception:   # noqa: BLE001
        return datetime.fromtimestamp(jusqu_a, timezone.utc).strftime('%d/%m/%Y %H:%M UTC')


def _tracer(desk, a, ident, revision, action, donnees, maintenant):
    desk.db.execute('INSERT OR REPLACE INTO file56100_historique VALUES(?,?,?,?,?,?,?)',
                    (CABINET, ident, revision, action, a.compte, int(maintenant), canon(donnees)))


def commande(desk, acteur, ident, sous, data, maintenant=None):
    """``POST …/{id}/decision|enregistrer|deposer`` : décision réellement confirmée, version utile à l'écran, ou refus (409 sans effet)."""
    a = _acteur(acteur)
    maintenant = int(_maintenant() if maintenant is None else maintenant)
    if sous not in ECRITURES or not IDENT.fullmatch(str(ident or '')) or not isinstance(data, dict):
        raise Refus(404, 'introuvable')
    action, revision, cle, corps, jusqu_a = _valider(sous, data, maintenant)
    kind = _kind_de(ident)
    if kind not in ('proposition', 'imap'):
        raise Refus(404, 'introuvable')
    if action != 'reporter' and not peut_deposer(desk, a):
        ligne = _ligne_projetee(desk, ident) if kind == 'proposition' else None
        if kind == 'proposition':
            exiger(desk, a, ligne)        # objet invisible : 404 neutre avant le refus de rôle
        raise Refus(403, 'role_insuffisant')
    if action == 'reporter' and corps is None and jusqu_a is None:
        raise Refus(422, 'report_invalide')
    if corps is not None and kind == 'proposition' and len(corps) > MAX_CORPS:
        raise Refus(422, 'texte_trop_long')
    sig = empreinte((ident, sous, action, revision, corps, jusqu_a))
    if kind == 'proposition':
        if action == 'deposer':
            return _deposer_proposition(desk, a, ident, revision, cle, corps, sig, maintenant)
        return _decider_proposition(desk, a, ident, action, revision, cle, corps, jusqu_a, sig, maintenant)
    return _decider_imap(desk, a, ident, action, revision, cle, corps, jusqu_a, sig, maintenant)


def _decider_proposition(desk, a, ident, action, revision, cle, corps, jusqu_a, sig, maintenant):
    """Enregistrer / Retenir / Écarter / Reporter une proposition interne : producteur, projection, trace et reçu dans UNE transaction."""
    db = desk.db
    db.commit()
    db.execute('BEGIN IMMEDIATE')
    try:
        ligne = _ligne_projetee(desk, ident)
        if ligne is None or ligne['kind'] != 'proposition':
            raise Refus(404, 'introuvable')
        exiger(desk, a, ligne)                                    # accès revérifié au moment d'agir, et avant de rendre un ancien reçu
        recu = _recu_anterieur(desk, a, cle, sig)
        if recu is None:
            recu = _appliquer_proposition(desk, a, ligne, action, revision, corps, jusqu_a, maintenant)
            _noter_recu(desk, a, cle, sig, recu, maintenant)
        db.commit()
    except BaseException:
        db.rollback()
        raise
    if recu.get('etat') in ('retenu', 'ecarte', 'enregistre'):
        desk.audit('relecture56100_' + action, {'id': ident, 'revision': revision, 'par': a.compte[:80]})
    return recu


def _appliquer_proposition(desk, a, ligne, action, revision, corps, jusqu_a, maintenant):
    cle_mail = ligne['mail_key']
    row = desk.db.execute('SELECT data FROM manual_drafts WHERE mail_key=?', (cle_mail,)).fetchone()
    try:
        data = json.loads(row[0]) if row else None
    except (TypeError, ValueError):
        data = None
    if not isinstance(data, dict) or id_proposition(cle_mail, data) != ligne['id']:
        raise Refus(409, 'version_perimee')                       # autre projet (relance) ou projet retiré : rien n'est écrasé
    mid = str(data.get('matter') or '')
    if mid != ligne['matter']:
        exiger(desk, a, {**ligne, 'matter': mid})
    if revision_proposition(data) != revision:
        raise Refus(409, 'version_perimee')
    result = dict(data.get('result') or {})
    decision = data.get('decision56100') if isinstance(data.get('decision56100'), dict) else {}
    etat_actuel = 'retenu' if decision.get('etat') == 'retenu' else ('a_relire' if result.get('requires_decision') else '')
    if etat_actuel != 'a_relire':
        raise Refus(409, 'version_perimee')
    now_iso = datetime.fromtimestamp(maintenant, timezone.utc).isoformat()
    if action == 'reporter':
        desk.db.execute('INSERT INTO file56100_reports VALUES(?,?,?,?,?) ON CONFLICT(cabinet,acteur,item) DO UPDATE SET '
                        'revision=excluded.revision,jusqu_a=excluded.jusqu_a', (CABINET, a.compte, ligne['id'], revision, jusqu_a))
        if ligne['revision'] != revision:                          # projection en retard sur le producteur : réalignée
            desk.db.execute('UPDATE file56100_items SET revision=? WHERE cabinet=? AND id=?', (revision, CABINET, ligne['id']))
        return {'id': ligne['id'], 'etat': 'reporte', 'revision': revision, 'jusqu_a': jusqu_a,
                'jusqu_a_local': _local_jusqu_a(desk, a.compte, jusqu_a), 'message': MESSAGES['reporte']}
    if action == 'ecarter':
        _tracer(desk, a, ligne['id'], revision, 'ecarter', {'corps': result.get('body') or '', 'destinataires': data.get('recipients') or [],
                                                           'dossier': mid, 'courriel': cle_mail}, maintenant)
        n = desk.db.execute('DELETE FROM manual_drafts WHERE mail_key=? AND data=?', (cle_mail, row[0])).rowcount
        if n != 1:
            raise Refus(409, 'version_perimee')
        desk.db.execute("UPDATE file56100_items SET etat='ecarte' WHERE cabinet=? AND id=?", (CABINET, ligne['id']))
        return {'id': ligne['id'], 'etat': 'ecarte', 'revision': revision, 'message': MESSAGES['ecarte']}
    result['body'] = corps
    if action == 'retenir':
        result['requires_decision'] = False
        result['limits'] = list(result.get('limits') or []) + ['Texte relu et retenu par l’avocat ; dépôt dans Brouillons sur sa seule demande.']
        data['decision56100'] = {'etat': 'retenu', 'par': a.compte[:120], 'le': now_iso, 'revision_relue': revision}
    data['result'] = result
    data['decided_by'] = a.compte[:120] if action == 'retenir' else data.get('decided_by')
    if data.get('decided_by') is None:
        data.pop('decided_by', None)
    texte = json.dumps(data, ensure_ascii=False)
    n = desk.db.execute('UPDATE manual_drafts SET data=?,updated=? WHERE mail_key=? AND data=?', (texte, now_iso, cle_mail, row[0])).rowcount
    if n != 1:
        raise Refus(409, 'version_perimee')
    nouvelle = revision_proposition(data)
    etat = 'retenu' if action == 'retenir' else 'a_relire'
    desk.db.execute('UPDATE file56100_items SET revision=?,etat=? WHERE cabinet=? AND id=? AND etat=?', (nouvelle, etat, CABINET, ligne['id'], 'a_relire'))
    if action == 'retenir':
        _tracer(desk, a, ligne['id'], nouvelle, 'retenir', {'corps': corps, 'destinataires': data.get('recipients') or [], 'dossier': mid,
                                                            'courriel': cle_mail}, maintenant)
        return {'id': ligne['id'], 'etat': 'retenu', 'revision': nouvelle, 'message': MESSAGES['retenu']}
    return {'id': ligne['id'], 'etat': 'enregistre', 'revision': nouvelle, 'corps': corps, 'message': MESSAGES['enregistre']}


def _intention_active(desk, item, action):
    row = desk.db.execute("SELECT * FROM file56100_intentions WHERE cabinet=? AND item=? AND action=? AND etat IN ('en_cours','a_verifier')",
                          (CABINET, item, action)).fetchone()
    return dict(row) if row else None


def _clore_intention(desk, ident, etat, preuve, maintenant):
    desk.db.execute('UPDATE file56100_intentions SET etat=?,preuve=?,updated=? WHERE cabinet=? AND id=?',
                    (etat, canon(preuve), int(maintenant), CABINET, ident))


def _deposer_proposition(desk, a, ident, revision, cle, corps, sig, maintenant):
    """Déposer dans Brouillons : intention durable écrite d'abord, dépôt IMAP hors transaction, « Déposé » après relecture chez le fournisseur.
    Une double demande ne crée qu'un brouillon ; un doute donne « À vérifier » et bloque un nouveau dépôt. Rien n'est envoyé."""
    db = desk.db
    db.commit()
    db.execute('BEGIN IMMEDIATE')
    try:
        ligne = _ligne_projetee(desk, ident)
        if ligne is None or ligne['kind'] != 'proposition':
            raise Refus(404, 'introuvable')
        exiger(desk, a, ligne)
        recu = _recu_anterieur(desk, a, cle, sig)
        if recu is not None:
            db.commit()
            return recu
        active = _intention_active(desk, ident, 'deposer')
        if active is not None:
            db.commit()
            etat = active['etat']
            return {'id': ident, 'etat': etat, 'revision': active['revision'], 'message': MESSAGES[etat]}
        data = _producteur(desk, ligne['mail_key'])
        if data is None or id_proposition(ligne['mail_key'], data) != ident or revision_proposition(data) != revision:
            raise Refus(409, 'version_perimee')
        if str(data.get('matter') or '') != ligne['matter']:
            exiger(desk, a, {**ligne, 'matter': str(data.get('matter') or '')})
        result = data.get('result') if isinstance(data.get('result'), dict) else {}
        texte = corps if corps is not None else str(result.get('body') or '')
        if not texte.strip():
            raise Refus(422, 'texte_invalide')
        intention = 'd-' + secrets.token_hex(12)
        db.execute("INSERT INTO file56100_intentions VALUES(?,?,?,?,?,?,?,?,'en_cours','{}',?,?)",
                   (CABINET, intention, ident, a.compte, 'deposer', revision, cle, sig, maintenant, maintenant))
        db.commit()
    except BaseException:
        db.rollback()
        raise
    # effet distant hors transaction SQLite ; la pause et l'arrêt sont relus juste avant l'APPEND (deposit_draft → pause5629.checkpoint)
    from . import reponses5671
    try:
        from .pause5629 import checkpoint
        checkpoint(desk)
        sortie = reponses5671.deposer(desk, a.compte, ligne['mail_key'], body=texte, revision=revision, projeter=False)
    except Stop as ex:
        code = str(ex).split(':')[0]
        etat = 'a_verifier' if code in INCERTAINS else 'echec'
        _fin_depot(desk, a, ident, intention, etat, {'raison': code}, cle, sig, revision, maintenant)
        if etat == 'a_verifier':
            return {'id': ident, 'etat': 'a_verifier', 'revision': revision, 'message': MESSAGES['a_verifier']}
        raise (_refus_imap(ex) if code in ('introuvable', 'controle_indisponible') or code in PERIMES else Refus(422, code)) from None
    except Exception:   # noqa: BLE001 — réponse perdue ou messagerie coupée : jamais rejoué en aveugle
        _fin_depot(desk, a, ident, intention, 'a_verifier', {'raison': 'reponse_perdue'}, cle, sig, revision, maintenant)
        return {'id': ident, 'etat': 'a_verifier', 'revision': revision, 'message': MESSAGES['a_verifier']}
    preuve = {'uid': str(sortie.get('uid') or ''), 'dossier': str(sortie.get('dossier') or ''), 'verifie': sortie.get('brouillon_imap') == 'verifie'}
    etat = 'depose' if preuve['verifie'] else 'a_verifier'
    return _fin_depot(desk, a, ident, intention, etat, preuve, cle, sig, revision, maintenant, texte=texte)


def _fin_depot(desk, a, ident, intention, etat, preuve, cle, sig, revision, maintenant, texte=None):
    db = desk.db
    try:
        db.rollback()
    except sqlite3.Error:
        pass
    db.execute('BEGIN IMMEDIATE')
    try:
        _clore_intention(desk, intention, 'fait' if etat == 'depose' else etat, preuve, maintenant)
        recu = {'id': ident, 'etat': etat, 'revision': revision, 'message': MESSAGES.get(etat, '')}
        if etat == 'depose':
            ligne = _ligne_projetee(desk, ident)
            db.execute("UPDATE file56100_items SET etat='depose' WHERE cabinet=? AND id=?", (CABINET, ident))
            _tracer(desk, a, ident, revision, 'deposer', {'corps': texte or '', 'courriel': (ligne or {}).get('mail_key', ''), 'preuve': preuve}, maintenant)
            recu['uid'] = preuve.get('uid', '')
        if etat in ('depose', 'a_verifier'):
            _noter_recu(desk, a, cle, sig, recu, maintenant)       # même clé : même réponse, aucun nouveau dépôt
        db.commit()
    except BaseException:
        db.rollback()
        raise
    desk.audit('relecture56100_depot', {'id': ident, 'etat': etat, 'par': a.compte[:80]})
    if etat == 'echec':
        projeter_apres_ecriture(desk, ((_ligne_projetee(desk, ident) or {}).get('mail_key') or ''))
    return recu


def _decider_imap(desk, a, ident, action, revision, cle, corps, jusqu_a, sig, maintenant):
    """Brouillon IMAP : Enregistrer passe par une intention durable et une relecture distante ; Retenir, Écarter et Reporter sont des
    décisions locales (le brouillon reste dans la messagerie, rien n'est supprimé ni envoyé)."""
    from . import drafts440
    if action == 'deposer':
        raise Refus(422, 'deja_dans_brouillons')
    db = desk.db
    prior = _recu_anterieur(desk, a, cle, sig)
    trouve = _trouver_imap(desk, a, ident)    # accès revérifié avant l'effet et avant de rendre un ancien reçu
    if trouve is None:
        raise Refus(404, 'introuvable')
    if prior is not None:
        return prior
    validity, uid = _lire_revision_imap(revision)
    if trouve['revision'] != revision:
        raise Refus(409, 'version_perimee')
    try:
        courant = drafts440.get_draft(desk.c['mail'], uid, validity, garde=_garde_brouillon(desk, a))
    except Stop as ex:
        raise _refus_imap(ex) from None
    if action == 'reporter':
        db.execute('INSERT INTO file56100_reports VALUES(?,?,?,?,?) ON CONFLICT(cabinet,acteur,item) DO UPDATE SET '
                   'revision=excluded.revision,jusqu_a=excluded.jusqu_a', (CABINET, a.compte, ident, revision, jusqu_a))
        recu = {'id': ident, 'etat': 'reporte', 'revision': revision, 'jusqu_a': jusqu_a,
                'jusqu_a_local': _local_jusqu_a(desk, a.compte, jusqu_a), 'message': MESSAGES['reporte']}
        _noter_recu(desk, a, cle, sig, recu, maintenant)
        db.commit()
        return recu
    nouvelle = revision
    if corps is not None and corps.strip() != str(courant.get('body') or '').strip():
        if not courant.get('editable', True):
            raise Refus(422, 'piece_jointe_non_modifiable_ici')
        sortie = _enregistrer_imap(desk, a, ident, trouve, courant, revision, corps, cle, sig, maintenant, noter=action == 'enregistrer')
        if sortie.get('etat') == 'a_verifier' or action == 'enregistrer':
            return sortie
        nouvelle = sortie['revision']
    elif action == 'enregistrer':
        recu = {'id': ident, 'etat': 'enregistre', 'revision': revision, 'corps': str(courant.get('body') or ''), 'message': MESSAGES['enregistre']}
        _noter_recu(desk, a, cle, sig, recu, maintenant)
        db.commit()
        return recu
    etat = 'retenu' if action == 'retenir' else 'ecarte'
    db.commit()
    db.execute('BEGIN IMMEDIATE')
    try:
        _upsert(desk, {**trouve, 'ref_source': 'imap:%s:%s' % (trouve['dossier_imap'], trouve['message_id'] or trouve['uid']), 'revision': nouvelle,
                       'etat': etat, 'search_norm': trouve['search_norm']})
        _tracer(desk, a, ident, nouvelle, action, {'objet': trouve['titre'], 'a': trouve.get('a', ''), 'courriel': trouve.get('mail_key', '')}, maintenant)
        recu = {'id': ident, 'etat': etat, 'revision': nouvelle, 'message': MESSAGES[etat] if etat == 'retenu' else
                'Brouillon retiré de la relecture. Il reste dans la messagerie.'}
        _noter_recu(desk, a, cle, sig, recu, maintenant)
        db.commit()
    except BaseException:
        db.rollback()
        raise
    desk.audit('relecture56100_' + action, {'id': ident, 'revision': nouvelle, 'par': a.compte[:80]})
    return recu


def _enregistrer_imap(desk, a, ident, trouve, courant, revision, corps, cle, sig, maintenant, noter=True):
    from . import drafts440
    db = desk.db
    db.commit()
    db.execute('BEGIN IMMEDIATE')
    try:
        active = _intention_active(desk, ident, 'enregistrer')
        if active is not None:
            db.commit()
            return {'id': ident, 'etat': active['etat'], 'revision': active['revision'], 'message': MESSAGES.get(active['etat'], '')}
        intention = 'e-' + secrets.token_hex(12)
        db.execute("INSERT INTO file56100_intentions VALUES(?,?,?,?,?,?,?,?,'en_cours','{}',?,?)",
                   (CABINET, intention, ident, a.compte, 'enregistrer', revision, cle, sig, maintenant, maintenant))
        db.commit()
    except BaseException:
        db.rollback()
        raise
    try:
        from .pause5629 import checkpoint
        checkpoint(desk)
        out = drafts440.save_draft(desk.c['mail'], {'uid': courant['uid'], 'uidvalidity': courant['uidvalidity'], 'revision': courant['revision'],
                                                    'to': courant.get('to', ''), 'cc': courant.get('cc', ''), 'bcc': courant.get('bcc', ''),
                                                    'subject': courant.get('subject', ''), 'body': corps}, garde=_garde_brouillon(desk, a))
    except Stop as ex:
        code = str(ex).split(':')[0]
        etat = 'a_verifier' if code in INCERTAINS else 'echec'
        _fin_imap(desk, a, ident, intention, etat, {'raison': code}, cle, sig, None, maintenant)
        if etat == 'a_verifier':
            return {'id': ident, 'etat': 'a_verifier', 'revision': revision, 'message': MESSAGES['a_verifier']}
        raise (_refus_imap(ex) if code in ('introuvable', 'controle_indisponible') or code in PERIMES else Refus(422, code)) from None
    except Exception:   # noqa: BLE001 — réponse perdue : à vérifier, jamais rejoué
        _fin_imap(desk, a, ident, intention, 'a_verifier', {'raison': 'reponse_perdue'}, cle, sig, None, maintenant)
        return {'id': ident, 'etat': 'a_verifier', 'revision': revision, 'message': MESSAGES['a_verifier']}
    nouvelle = revision_imap(out['uidvalidity'], out['uid'])
    recu = {'id': ident, 'etat': 'enregistre', 'revision': nouvelle, 'corps': corps, 'message': MESSAGES['enregistre'],
            'ancien_retire': bool(out.get('previous_removed'))}
    _fin_imap(desk, a, ident, intention, 'fait', {'uid': out['uid'], 'uidvalidity': out['uidvalidity']}, cle, sig, recu if noter else None, maintenant)
    return recu


def _fin_imap(desk, a, ident, intention, etat, preuve, cle, sig, recu, maintenant):
    db = desk.db
    db.execute('BEGIN IMMEDIATE')
    try:
        _clore_intention(desk, intention, etat, preuve, maintenant)
        if recu is not None:
            _noter_recu(desk, a, cle, sig, recu, maintenant)
        elif etat == 'a_verifier':
            _noter_recu(desk, a, cle, sig, {'id': ident, 'etat': 'a_verifier', 'message': MESSAGES['a_verifier']}, maintenant)
        db.commit()
    except BaseException:
        db.rollback()
        raise


def ecarter_cle(desk, owner, cle, data_vue):
    """Ancienne action « Écarter » (page du courriel, cartes de la file) : même transition que le lecteur, dans une transaction, sur la
    version lue par l'appelant ; une version remplacée entre-temps n'est jamais effacée (409)."""
    revision = revision_proposition(data_vue)
    db = desk.db
    db.commit()
    db.execute('BEGIN IMMEDIATE')
    try:
        row = db.execute('SELECT data FROM manual_drafts WHERE mail_key=?', (cle,)).fetchone()
        try:
            data = json.loads(row[0]) if row else None
        except (TypeError, ValueError):
            data = None
        if not isinstance(data, dict):
            raise Stop('projet_prudent_absent')
        if revision_proposition(data) != revision:
            raise Stop('version_perimee')
        ident = id_proposition(cle, data)
        result = data.get('result') if isinstance(data.get('result'), dict) else {}
        db.execute('INSERT OR REPLACE INTO file56100_historique VALUES(?,?,?,?,?,?,?)',
                   (CABINET, ident, revision, 'ecarter', str(owner)[:120], int(_maintenant()),
                    canon({'corps': result.get('body') or '', 'destinataires': data.get('recipients') or [], 'dossier': data.get('matter') or '',
                           'courriel': cle})))
        if db.execute('DELETE FROM manual_drafts WHERE mail_key=? AND data=?', (cle, row[0])).rowcount != 1:
            raise Stop('version_perimee')
        db.execute("UPDATE file56100_items SET etat='ecarte' WHERE cabinet=? AND id=?", (CABINET, ident))
        db.commit()
    except BaseException:
        db.rollback()
        raise


def verifier_intention(desk, acteur, ident):
    """Après un doute (« À vérifier ») : l'avocat a contrôlé la messagerie ; l'intention est close, un nouveau dépôt redevient possible."""
    a = _acteur(acteur)
    if not peut_deposer(desk, a):
        raise Refus(403, 'role_insuffisant')
    desk.db.execute("UPDATE file56100_intentions SET etat='echec',updated=? WHERE cabinet=? AND item=? AND etat='a_verifier'",
                    (int(_maintenant()), CABINET, ident))
    desk.db.commit()


# ------------------------------------------------------------------------------------------------------------- HTTP
def _sortie(data, status='200 OK'):
    from .web440 import json_out
    return {**json_out(data, status), 'headers': [('Cache-Control', 'private, no-store')]}


def repondre(env, desk, auth, prefix, name, args, method):
    """Routes ``/api440/m56100/relecture…`` (liste fermée ; rôles dans ``standalone_auth.allowed``)."""
    from .web440 import check_post
    from .web567 import actor
    try:
        compte, role = actor(env)
        if not autoriser_route(role, method, name):
            raise Refus(404, 'introuvable')
        a = _acteur((compte, role))
        reste = name.split('?', 1)[0][len(PREFIXE):].strip('/')
        if not reste:
            return _sortie(liste(desk, a, args, prefix))
        ident, _, sous = reste.partition('/')
        if method in ('GET', 'HEAD'):
            if sous:
                return _sortie(section(desk, a, ident, str(args.get('revision') or ''), sous))
            return _sortie(detail(desk, a, ident, prefix))
        if int(env.get('CONTENT_LENGTH') or 0) > MAX_POST:
            raise Refus(422, 'requete_trop_longue')
        data = check_post(env, auth)
        return _sortie(commande(desk, a, ident, sous, data))
    except Refus as ex:
        code = ex.code.split(':')[0]
        return _sortie({'error': code, 'message': _message(code)}, STATUTS.get(ex.status, '422 Unprocessable Entity'))
    except Stop as ex:
        code = str(ex).split(':')[0]
        if code in ('dossier_reserve', 'introuvable', 'projet_prudent_absent', 'courriel_introuvable'):
            return _sortie({'error': 'introuvable', 'message': _message('introuvable')}, STATUTS[404])
        if code in ('origine_ou_csrf_refuse', 'role_insuffisant', 'acteur_requis'):
            return _sortie({'error': code, 'message': _message(code)}, STATUTS[403])
        if code in PERIMES:
            return _sortie({'error': 'version_perimee', 'message': _message('version_perimee')}, STATUTS[409])
        if code in ('controle_acces_indisponible', 'controle_indisponible'):
            return _sortie({'error': 'indisponible', 'message': _message('controle_indisponible')}, STATUTS[503])
        return _sortie({'error': code, 'message': _message(code)}, STATUTS[422])


TEXTES = {'introuvable': 'Élément introuvable.', 'version_perimee': 'Ce projet a changé depuis son ouverture : votre saisie est conservée, rechargez la version actuelle.',
          'cle_reutilisee': 'Cette demande a déjà été utilisée pour une autre action.', 'role_insuffisant': 'Votre compte ne permet pas cette action.',
          'controle_indisponible': 'Contrôle des droits momentanément indisponible : rien n’est affiché ni modifié.',
          'source_indisponible': 'La messagerie ne répond pas. Le projet local reste lisible ; réessayez.',
          'curseur_invalide': 'La liste a changé : elle est rechargée depuis le début.', 'texte_invalide': 'Le texte est vide ou trop long.',
          'report_invalide': 'Choisissez une date de report dans l’année qui vient.', 'version_requise': 'Version affichée manquante : rechargez le projet.',
          'deja_dans_brouillons': 'Ce brouillon est déjà dans la messagerie.', 'saisie_non_enregistree': 'Enregistrez ou abandonnez d’abord votre saisie.'}


def _message(code):
    return TEXTES.get(code) or _humain(code)


# ------------------------------------------------------------------------------------------------------------- gabarits
def lecteur_html(prefix, suffixe_ids=''):
    """Gabarit du lecteur (même structure pour la page Courriels et la fenêtre d'Aujourd'hui) ; IDs DOM distincts par instance.
    Le contenu reçu est écrit par ``v56100-lecteur.js`` avec ``textContent`` et ``textarea.value`` : aucun HTML de courriel injecté."""
    s = re.sub(r'[^A-Za-z0-9_-]', '', str(suffixe_ids or ''))[:40]
    return ('<article class="ax100-lecteur" data-ax100-lecteur aria-labelledby="ax100-sujet%(s)s">'
            '<header class="ax100-entete">'
            '<button type="button" class="ax-btn ghost ax100-retour" data-ax100-retour>Retour à la liste</button>'
            '<h2 id="ax100-sujet%(s)s" data-ax100-sujet tabindex="-1">Choisissez un courriel</h2>'
            '<p class="ax100-meta" data-ax100-meta>Projet de réponse, message reçu, pièces et sources : un clic suffit. Rien n’est envoyé.</p>'
            '<div class="ax100-onglets" role="group" aria-label="Affichage">'
            '<button type="button" class="ax-btn ghost" data-section="projet" aria-pressed="true">Projet de réponse</button>'
            '<button type="button" class="ax-btn ghost" data-section="message" aria-pressed="false">Message reçu</button>'
            '<button type="button" class="ax-btn ghost" data-section="pieces" aria-pressed="false">Pièces</button>'
            '<button type="button" class="ax-btn ghost" data-section="sources" aria-pressed="false">Sources</button>'
            '<a class="ax-btn ghost ax100-plein" data-ax100-plein hidden href="%(p)s/courriels">Ouvrir en pleine page</a></div></header>'
            '<div class="ax100-corps" data-ax100-corps data-vue-texte="projet">'
            '<div class="ax100-projet" data-ax100-projet><label for="ax100-texte%(s)s">Texte du projet</label>'
            '<textarea id="ax100-texte%(s)s" data-ax100-texte rows="12" spellcheck="true" readonly></textarea></div>'
            '<div class="ax100-secondaire" data-ax100-secondaire tabindex="0" hidden></div></div>'
            '<footer class="ax100-actions" data-ax100-actions>'
            '<p role="status" aria-live="polite" class="ax100-statut" data-ax100-statut></p>'
            '<button type="button" class="ax-btn ghost" data-action="enregistrer" disabled>Enregistrer</button>'
            '<button type="button" class="ax-btn" data-action="retenir" disabled>Retenir</button>'
            '<button type="button" class="ax-btn ghost" data-action="ecarter" disabled>Écarter</button>'
            '<span class="ax100-report"><button type="button" class="ax-btn ghost" data-action="reporter" aria-expanded="false" disabled>Reporter</button>'
            '<span class="ax100-report-choix" data-ax100-report hidden role="group" aria-label="Reporter jusqu’à">'
            '<button type="button" class="ax-btn ghost" data-report="apres_midi">Cet après-midi (14 h)</button>'
            '<button type="button" class="ax-btn ghost" data-report="demain">Demain 9 h</button>'
            '<button type="button" class="ax-btn ghost" data-report="lundi">Lundi 9 h</button></span></span>'
            '<button type="button" class="ax-btn ghost ax100-deposer" data-action="deposer" disabled hidden>Déposer dans Brouillons (sans envoi)</button>'
            '</footer></article>') % {'s': s, 'p': e(prefix, quote=True)}


def _ligne_html(x, courant=''):
    return ('<li data-review-id="%s" data-maj="%d"><a href="%s" data-review-id="%s"%s><span class="ax100-l1"><strong>%s</strong>'
            '<em class="ax100-badge ax100-%s">%s</em></span><span class="ax100-l2">%s · %s · %s</span></a></li>') % (
        e(x['id'], quote=True), int(x.get('maj') or 0), e(x['href'], quote=True), e(x['id'], quote=True),
        ' aria-current="true"' if x['id'] == courant else '', e(x['objet']), e(x['type'], quote=True), e(x['type_libelle']),
        e(x['dossier']), e(x['date_affichee']), e(x['statut_libelle']))


def tete_html(prefix):
    """Script du lecteur, servi seulement sur les pages qui le montent (Courriels « À relire » ; Aujourd'hui l'ajoute pour sa fenêtre)."""
    return '<script defer src="%s/static/v56100-lecteur.js"></script>' % e(prefix, quote=True)


def _diagnostic(desk, prefix):
    """Détail replié de l'en-tête : conflits d'intérêts à examiner et lien vers la messagerie (diagnostic des brouillons)."""
    out = ''
    try:
        from .conflicts500 import banner_html
        out += banner_html(desk, prefix)
    except Exception:   # noqa: BLE001 — bannière indisponible : la relecture reste utilisable
        pass
    try:
        from .workstation import external_links
        liens = external_links(desk)
        lien = liens.get('roundcube_drafts') or liens.get('roundcube') or ''
        if lien:
            out += '<p><a class="ax-btn ghost" target="_blank" rel="noopener" href="%s">Ouvrir la messagerie</a></p>' % e(lien, quote=True)
    except Exception:   # noqa: BLE001
        pass
    return out


def _etat_local_texte(local):
    if local.get('etat') == 'synchronisation_en_cours':
        texte = 'Synchronisation en cours : la liste des propositions se complète.'
    else:
        quand = str(local.get('synchronise_le') or '')[:16].replace('T', ' ')
        texte = 'Propositions à jour' + ((' (dernier passage : %s UTC)' % quand) if quand else '') + '.'
    if local.get('anomalies'):
        texte += ' %d entrée(s) illisible(s) isolée(s) pour la maintenance.' % local['anomalies']
    return texte


def page_html(desk, prefix, acteur, args):
    """Vue « À relire » de la nouvelle interface : liste unique à gauche (première page locale rendue par le serveur, liens de repli
    ``/courriels?item=…``), lecteur à droite. Les brouillons IMAP sont lus par un appel séparé du script, jamais pendant ce rendu."""
    a = _acteur(acteur)
    source = str(args.get('source') or 'tous')
    source = source if source in SOURCES else 'tous'
    q = str(args.get('q') or '')[:120]
    item = str(args.get('item') or '')
    item = item if IDENT.fullmatch(item) else ''
    erreur = ''
    try:
        if source == 'brouillon':
            data = {'lignes': [], 'curseur': None, 'total': None, 'sources': {'local': _etat_local(desk)}}
        else:
            data = liste(desk, a, {'source': source, 'q': q}, prefix)
    except Refus as ex:
        data, erreur = {'lignes': [], 'curseur': None, 'total': None, 'sources': {}}, _message(ex.code.split(':')[0])
    lignes = ''.join(_ligne_html(x, item) for x in data['lignes'])
    filtres = ''.join('<a href="%s" data-ax100-filtre="%s"%s>%s</a>' % (
        e(prefix + '/courriels?' + urlencode({'source': cle, **({'q': q} if q else {})}), quote=True), cle,
        ' aria-current="page"' if cle == source else '', texte) for cle, texte in (('tous', 'Tout'), ('proposition', 'Propositions'), ('brouillon', 'Brouillons')))
    total = data.get('total')
    lecteur = lecteur_html(prefix, '')
    if item and _kind_de(item) == 'proposition':
        try:
            d = detail(desk, a, item, prefix)
        except Refus:
            d = None
        if d is not None:   # repli sans script : le projet est lisible et le détail historique reste à un clic
            lecteur = lecteur.replace('tabindex="-1">Choisissez un courriel</h2>', 'tabindex="-1">%s</h2>' % e(d['sujet']), 1)
            lecteur = lecteur.replace('readonly></textarea>', 'readonly>%s</textarea>' % e(d['corps']), 1)
            if d.get('liens', {}).get('pleine_page'):
                lecteur = lecteur.replace(' data-ax100-plein hidden href="%s/courriels"' % e(prefix, quote=True),
                                          ' data-ax100-plein href="%s"' % e(d['liens']['pleine_page'], quote=True), 1)
    return ('<section class="ax100-relecture" data-ax100-page data-ax100-relecture data-source="%s" data-q="%s" data-curseur="%s" '
            'data-item="%s" aria-label="Courriels à relire">'
            '<header class="ax100-tete"><h1>À relire <span class="ax100-total" data-ax100-total aria-live="polite">%s</span></h1>'
            '<nav class="ax100-filtres" aria-label="Filtres de relecture">%s</nav>'
            '<form method="get" role="search" class="ax100-recherche" data-ax100-recherche action="%s/courriels">'
            '<input type="hidden" name="source" value="%s"><label for="ax100-q">Rechercher un projet</label>'
            '<input id="ax100-q" name="q" type="search" value="%s" maxlength="120"><button type="submit" class="ax-btn ghost">Rechercher</button></form>'
            '<details class="ax100-diagnostic"><summary>État des sources</summary><p data-ax100-sources>%s</p>'
            '<p data-ax100-imap>Brouillons de la messagerie : lecture séparée, après l’affichage des propositions.</p>%s</details>%s</header>'
            '<nav class="ax100-liste" aria-label="Projets à relire" data-ax100-zone-liste><ol data-ax100-lignes>%s</ol>'
            '<p class="ax100-vide" data-ax100-vide tabindex="-1"%s>Tout est relu dans ce filtre.</p>'
            '<button type="button" class="ax-btn ghost" data-ax100-suite%s>Afficher la suite</button></nav>%s</section>') % (
        e(source, quote=True), e(q, quote=True), e(data.get('curseur') or '', quote=True), e(item, quote=True),
        ('%d' % total) if isinstance(total, int) else '…', filtres, e(prefix, quote=True), e(source, quote=True), e(q, quote=True),
        e(_etat_local_texte((data.get('sources') or {}).get('local') or {})), _diagnostic(desk, prefix),
        ('<p class="ax-alert" role="alert">%s</p>' % e(erreur)) if erreur else '', lignes,
        ' hidden' if lignes or source != 'proposition' else '', '' if data.get('curseur') else ' hidden', lecteur)


