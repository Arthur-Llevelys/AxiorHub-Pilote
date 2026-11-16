"""5.6.106 (lot L) : « Courriels » en vrai client de messagerie — liste, dossiers IMAP, actions groupées, densité.

- **Actions groupées** sur les courriels cochés : corbeille, indésirable, « pas indésirable » (retour en Boîte de réception), déplacer,
  archiver, lu, non lu, suivre, ne plus suivre, déplacer vers le sous-dossier du dossier client, classer dans le dossier client
  (conservation existante ``correspondances5643``). Une commande IMAP par dossier (ensemble d'UID), 200 courriels au plus (50 pour le
  classement, qui lit chaque message). La boîte est sélectionnée en écriture et son UIDVALIDITY comparée à celle de la liste affichée :
  différente, rien n'est fait (409, liste à recharger). Chaque courriel est contrôlé (droits, présence) avant l'effet ; l'effet est
  **constaté** ensuite chez le serveur (nouvelle recherche ou lecture des drapeaux) : le résultat dit exactement « 18 déplacés, 2 en
  échec (déjà supprimés) ». Un courriel interdit et un courriel absent donnent le même motif neutre.
- **Dossiers IMAP** : créer, renommer, supprimer (jamais les dossiers spéciaux ni « Dossiers clients ») ; nom en UTF-7 modifié
  (RFC 3501 § 5.1.3), séparateur lu au serveur (``LIST``), caractères interdits remplacés, doublons évités ; un dossier non vide n'est
  supprimé qu'après confirmation de son nombre de courriels, qui partent d'abord dans la Corbeille (récupérables).
- **Sous-dossier du dossier client** sous « Dossiers clients » (choix de l'avocat), créé au besoin ; le lien dossier ↔ sous-dossier est
  mémorisé (``boite56106_sous_dossiers``).
- **Densité** : réglage commun du lot D (``body[data-densite]``) ; aucun réglage propre à Courriels.

Garde-fous communs : rôles avocat et administrateur ; ``check_post`` ; corps borné ; clé d'idempotence sur chaque effet (même clé, même
demande : même réponse) ; ``pause5629.checkpoint`` avant chaque effet ; réponses ``Cache-Control: private, no-store`` ; aucune écriture
pendant une lecture (R05) ; schéma créé au démarrage (``Desk``). Rien n'est envoyé, rien n'est supprimé définitivement.
"""
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import re

from .common import Stop, load_matters
from . import mailbox as mbx

PREFIXE = 'm56106/boite/'
ROLES = ('administrateur', 'avocat')
ROUTES_GET = ('dossiers', 'sous-dossier')
ROUTES_POST = ('actions', 'dossier/creer', 'dossier/renommer', 'dossier/supprimer', 'dossier/tout-lu', 'sous-dossier', 'preferences')
MAX_ACTIONS = 200
MAX_CLASSER = 50
MAX_POST = 64 * 1024
PARENT_CLIENTS = 'Dossiers clients'
CLE = re.compile(r'^[A-Za-z0-9_-]{8,64}$')
ACTIONS = ('trash', 'junk', 'not_junk', 'move', 'archive', 'lu', 'non_lu', 'suivi', 'non_suivi', 'sous_dossier', 'classer', 'epingler', 'desepingler')
MAX_EPINGLES = 50       # épingles par personne (propres à AxiorHub : jamais un drapeau IMAP)
MAX_TOUT_LU = 5000      # « Tout le dossier comme lu » : courriels au plus, par commandes de 1 000 UID
CHAMPS_RECHERCHE = ('objet', 'corps', 'expediteur', 'destinataires')
RECHERCHE_DEFAUT = ('objet', 'expediteur')
DRAPEAUX = {'lu': ('+', '\\Seen'), 'non_lu': ('-', '\\Seen'), 'suivi': ('+', '\\Flagged'), 'non_suivi': ('-', '\\Flagged')}
SPECIAUX = ('inbox', 'drafts', 'sent', 'outbox', 'trash', 'junk', 'archive')
INTERDITS = re.compile(r'[\x00-\x1f\x7f/\\.*%"]')
TRIS = ('date_desc', 'date_asc', 'expediteur', 'objet')
MOIS = ('Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec')

SCHEMA = '''
CREATE TABLE IF NOT EXISTS boite56106_operations(compte TEXT NOT NULL, cle TEXT NOT NULL, empreinte TEXT NOT NULL, reponse TEXT NOT NULL,
  quand TEXT NOT NULL, PRIMARY KEY(compte, cle));
CREATE TABLE IF NOT EXISTS boite56106_sous_dossiers(matter_id TEXT NOT NULL, compte_imap TEXT NOT NULL, nom_imap TEXT NOT NULL,
  cree_par TEXT NOT NULL, cree_le TEXT NOT NULL, PRIMARY KEY(matter_id, compte_imap));
CREATE TABLE IF NOT EXISTS boite56106_epingles(proprietaire TEXT NOT NULL, compte_imap TEXT NOT NULL, cle TEXT NOT NULL,
  message_id TEXT NOT NULL DEFAULT '', dossier TEXT NOT NULL, uidvalidity TEXT NOT NULL DEFAULT '', uid TEXT NOT NULL DEFAULT '',
  cree_le TEXT NOT NULL, PRIMARY KEY(proprietaire, compte_imap, cle));
'''
MESSAGES = {'introuvable': 'Courriel ou dossier introuvable.', 'role_insuffisant': 'Action réservée aux avocats et aux administrateurs.',
            'controle_indisponible': 'Contrôle des droits momentanément indisponible : rien n’a été fait, réessayez.',
            'boite_reindexee': 'La boîte a changé depuis l’affichage de la liste (réindexée par le serveur) : rien n’a été fait. Rechargez la liste.',
            'cle_idempotence_requise': 'Page incomplète : rechargez-la.',
            'cle_idempotence_deja_utilisee': 'Cette demande a déjà été traitée avec d’autres valeurs : rechargez la page.',
            'trop_de_courriels': '200 courriels au plus par action.', 'trop_de_courriels_a_classer': '50 courriels au plus par classement.',
            'aucun_courriel': 'Cochez au moins un courriel.', 'action_inconnue': 'Action inconnue.',
            'dossier_special_protege': 'Ce dossier de la messagerie est protégé : il ne peut être ni renommé ni supprimé.',
            'nom_de_dossier_vide': 'Indiquez le nom du dossier.', 'dossier_avec_sous_dossiers': 'Ce dossier contient des sous-dossiers : videz-les d’abord.',
            'confirmation_requise': 'Confirmez la suppression.', 'dossier_deja_existant': 'Un dossier de ce nom existe déjà.',
            'creation_dossier_refusee': 'Le serveur de messagerie a refusé la création du dossier.',
            'renommage_refuse': 'Le serveur de messagerie a refusé le renommage.', 'suppression_refusee': 'Le serveur de messagerie a refusé la suppression.',
            'dossier_contient_courriels_reserves': 'Suppression impossible : certains courriels de ce dossier ne vous sont pas accessibles.',
            'dossier_a_choisir': 'Choisissez le dossier client.', 'corbeille_absente': 'Corbeille introuvable dans la messagerie.',
            'dossier_indesirables_absent': 'Dossier Indésirables introuvable dans la messagerie.', 'cible_a_choisir': 'Choisissez le dossier de destination.',
            'courriel_deja_dans_ce_dossier': 'Les courriels sont déjà dans ce dossier.',
            'trop_d_epingles': '50 courriels épinglés au plus : désépinglez-en d’abord.', 'champs_de_recherche_a_choisir': 'Cochez au moins un champ.',
            'trop_de_courriels_non_lus': 'Plus de 5 000 courriels non lus : marquez-les en plusieurs fois (par période, avec le filtre).',
            'route_inconnue': 'Route inconnue.', 'dossier_messagerie_inconnu': 'Dossier de messagerie introuvable.',
            'deja_dans_la_corbeille_suppression_definitive_dans_la_messagerie': 'Ces courriels sont déjà dans la Corbeille : la suppression '
            'définitive se fait dans la messagerie.'}
STATUTS = {'introuvable': '404 Not Found', 'dossier_messagerie_inconnu': '404 Not Found', 'route_inconnue': '404 Not Found',
           'role_insuffisant': '403 Forbidden', 'boite_reindexee': '409 Conflict', 'cle_idempotence_deja_utilisee': '409 Conflict',
           'confirmation_requise': '409 Conflict', 'dossier_deja_existant': '409 Conflict', 'controle_indisponible': '503 Service Unavailable'}
MOTIFS = {'absent': 'déjà déplacé ou supprimé', 'refuse': 'refusé par le serveur', 'non_constate': 'effet non constaté chez le serveur'}
VERBES = {'trash': ('déplacé dans la Corbeille', 'déplacés dans la Corbeille'), 'junk': ('classé indésirable', 'classés indésirables'),
          'not_junk': ('remis en Boîte de réception', 'remis en Boîte de réception'), 'move': ('déplacé', 'déplacés'),
          'archive': ('archivé', 'archivés'), 'lu': ('marqué lu', 'marqués lus'), 'non_lu': ('marqué non lu', 'marqués non lus'),
          'suivi': ('marqué pour suivi', 'marqués pour suivi'), 'non_suivi': ('suivi retiré', 'suivis retirés'),
          'sous_dossier': ('déplacé dans le sous-dossier du dossier client', 'déplacés dans le sous-dossier du dossier client'),
          'classer': ('mis en classement dans le dossier client', 'mis en classement dans le dossier client'),
          'epingler': ('épinglé', 'épinglés'), 'desepingler': ('désépinglé', 'désépinglés')}


def _now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def ensure_schema(desk):
    """Création idempotente, au démarrage (``Desk``) seulement — jamais pendant une lecture."""
    desk.db.executescript(SCHEMA)
    desk.db.commit()


def _compte(cfg):
    from .messages5697 import compte_de
    return compte_de(cfg)


# ------------------------------------------------------------------------------------------------ noms IMAP
def lister_imap(box):
    """``LIST "" "*"`` lu au serveur : [{'name' (décodé), 'brut' (UTF-7 modifié), 'sep', 'flags'}], séparateur compris."""
    from .setup import decode_utf7
    status, data = box.conn.list()
    if status != 'OK':
        raise Stop('liste_dossiers_imap_indisponible')
    out = []
    for ligne in data or ():
        if not isinstance(ligne, bytes):
            continue
        m = re.match(rb'\((.*?)\) ("(?:\\.|[^"])*"|NIL) (.*)', ligne)
        if not m:
            continue
        sep = m[2].decode('ascii')
        sep = '' if sep == 'NIL' else sep[1:-1].replace('\\\\', '\\').replace('\\"', '"')
        brut = m[3].decode('ascii').strip()
        if brut.startswith('"') and brut.endswith('"'):
            brut = brut[1:-1].replace('\\"', '"').replace('\\\\', '\\')
        out.append({'name': decode_utf7(brut), 'brut': brut, 'sep': sep, 'flags': m[1].decode().lower().split()})
    return out


def separateur(lignes):
    """Séparateur hiérarchique du serveur (le plus fréquent dans ``LIST``) ; « / » si le serveur n'en déclare aucun."""
    vus = [x['sep'] for x in lignes if x['sep']]
    return max(set(vus), key=vus.count) if vus else '/'


def prefixe_personnel(lignes, sep):
    """« INBOX. » quand tous les dossiers personnels vivent sous INBOX (Courier, certains Dovecot), sinon rien."""
    autres = [x['name'] for x in lignes if x['name'].upper() != 'INBOX']
    if autres and all(n.upper().startswith('INBOX' + sep) for n in autres):
        return 'INBOX' + sep
    return ''


def nom_sur(nom, sep='/'):
    """Nom d'un dossier lisible et sûr : espaces resserrés, séparateur et caractères interdits remplacés par « - », 80 caractères au plus."""
    nom = re.sub(r'\s+', ' ', str(nom or '')).strip()
    nom = INTERDITS.sub('-', nom)
    if sep:
        nom = nom.replace(sep, '-')
    nom = nom.strip(' -')[:80].strip()
    return nom


def nom_libre(existants, parent, nom, sep):
    """Nom complet sans doublon parmi les dossiers frères (comparaison sans casse) : « nom », « nom (2) », « nom (3) »…"""
    plies = {e.casefold() for e in existants}
    base = (parent + sep if parent else '') + nom
    if base.casefold() not in plies:
        return base
    for n in range(2, 1000):
        essai = '%s (%d)' % (base, n)
        if essai.casefold() not in plies:
            return essai
    raise Stop('dossier_deja_existant')


def _roles(box, cfg):
    from .boite5625 import _folder_rows
    return {r['name']: r['role'] for r in _folder_rows(box, cfg)}


def _protege(box, cfg, nom, lignes, sep):
    roles = _roles(box, cfg)
    pref = prefixe_personnel(lignes, sep)
    return roles.get(nom, '') in SPECIAUX or nom.upper() == 'INBOX' or nom in (PARENT_CLIENTS, pref + PARENT_CLIENTS)


def dossiers(desk, box=None):
    """Dossiers de ``boite5625.folders`` complétés : séparateur, profondeur, nom court, protégé, sous-dossiers, lien au dossier client."""
    from .boite5625 import folders
    cfg = desk.c['mail']
    own = box is None
    box = box or mbx.Mailbox(cfg)
    try:
        base = folders(desk, box)
        lignes = lister_imap(box)
        sep = separateur(lignes)
        pref = prefixe_personnel(lignes, sep)
        noms = [x['name'] for x in lignes]
        liens = {}
        try:
            for row in desk.db.execute('SELECT matter_id, nom_imap FROM boite56106_sous_dossiers WHERE compte_imap=?', (_compte(cfg),)):
                liens[row['nom_imap']] = row['matter_id']
        except Exception:   # noqa: BLE001 — table absente (démarrage incomplet) : aucun lien affiché, rien n'est écrit pendant la lecture
            liens = {}
        for f in base['folders']:
            court = f['name'][len(pref):] if pref and f['name'].startswith(pref) else f['name']
            f['profondeur'] = court.count(sep) if sep and f['role'] not in SPECIAUX else 0
            f['court'] = (court.rsplit(sep, 1)[-1] if sep else court) if not f['role'] else f['label']
            f['protege'] = f['role'] in SPECIAUX or f['name'].upper() == 'INBOX' or f['name'] == pref + PARENT_CLIENTS
            f['enfants'] = any(n.startswith(f['name'] + sep) for n in noms) if sep else False
            if f['name'] in liens:
                f['matter'] = liens[f['name']]
        base.update({'separateur': sep, 'prefixe': pref, 'parent_clients': pref + PARENT_CLIENTS})
        return base
    finally:
        if own:
            box.close()


# ------------------------------------------------------------------------------------------------ liste : tri, filtres, recherche
def _jour(valeur):
    valeur = str(valeur or '').strip()
    if not valeur:
        return None
    try:
        return date.fromisoformat(valeur[:10])
    except ValueError:
        raise Stop('date_invalide') from None


def _imap_date(d):
    return '%02d-%s-%04d' % (d.day, MOIS[d.month - 1], d.year)


def options_liste(args):
    """Options de ``boite5625.listing`` lues dans la requête (GET) : tri, filtres et recherche étendue, bornés."""
    get = lambda k: str((args or {}).get(k) or '')
    clean = lambda v: re.sub(r'\s+', ' ', v).strip()[:120]
    tri = get('tri') if get('tri') in TRIS else 'date_desc'
    du, au = _jour(get('du')), _jour(get('au'))
    if du and au and du > au:
        du, au = au, du
    return {'tri': tri, 'non_lus': get('non_lus') in ('1', 'oui'), 'suivis': get('suivis') in ('1', 'oui'), 'pj': get('pj') in ('1', 'oui'),
            'corps': clean(get('corps')), 'destinataire': clean(get('destinataire')), 'du': du, 'au': au,
            'dans': champs_recherche(get('dans'))}   # « Rechercher dans » (objet et expéditeur par défaut, comme avant)


def criteres(opts):
    """Critères IMAP (côté serveur) des filtres et de la recherche étendue ; le corps non ASCII part en littéral ``CHARSET UTF-8``
    (rendu à part : ``(critères, littéral)``) ; un destinataire non ASCII est filtré localement par ``boite5625.listing``."""
    out, litteral = [], None
    if not opts:
        return out, litteral
    if opts.get('non_lus'):
        out.append('UNSEEN')
    if opts.get('suivis'):
        out.append('FLAGGED')
    if opts.get('pj'):
        out += ['HEADER', 'Content-Type', mbx.imap_quote('multipart/mixed')]
    if opts.get('destinataire') and opts['destinataire'].isascii():
        out += ['TO', mbx.imap_quote(opts['destinataire'])]
    if opts.get('du'):
        out += ['SINCE', _imap_date(opts['du'])]
    if opts.get('au'):
        out += ['BEFORE', _imap_date(opts['au'] + timedelta(days=1))]   # BEFORE est exclusif : le jour choisi est compris
    if opts.get('corps'):
        if opts['corps'].isascii():
            out += ['BODY', mbx.imap_quote(opts['corps'])]
        else:
            litteral = opts['corps'].encode('utf-8')
    return out, litteral


def chercher(box, base, opts, sort=None):
    """UID SEARCH (ou UID SORT) dans la boîte déjà sélectionnée, avec le littéral UTF-8 en dernier critère s'il y a lieu."""
    extra, litteral = criteres(opts)
    tous = list(base) + extra
    if litteral is not None:
        tous = ['CHARSET', 'UTF-8'] + tous + ['BODY']
        box.conn.literal = litteral
    from .charge5693 import compter
    compter('imap')
    if sort:
        if tous[:2] == ['CHARSET', 'UTF-8']:
            tous = tous[2:]
        status, result = box.conn.uid('SORT', sort, 'UTF-8', *tous)
    else:
        status, result = box.conn.uid('SEARCH', None, *tous)
    if status != 'OK':
        raise Stop('recherche_imap_echouee')
    return result[0].decode().split() if result and result[0] else []


def peut_trier(box):
    from .webmail5628 import _caps
    return 'SORT' in _caps(box)


# ------------------------------------------------------------------------------------------------ idempotence
def _empreinte(corps):
    return hashlib.sha256(json.dumps(corps, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


def _recu(desk, owner, cle, corps):
    if not CLE.match(str(cle or '')):
        raise Stop('cle_idempotence_requise')
    row = desk.db.execute('SELECT empreinte, reponse FROM boite56106_operations WHERE compte=? AND cle=?', (owner, cle)).fetchone()
    if row is None:
        return None
    if row['empreinte'] != _empreinte(corps):
        raise Stop('cle_idempotence_deja_utilisee')
    return json.loads(row['reponse'])


def _garder(desk, owner, cle, corps, reponse):
    desk.db.execute('INSERT OR REPLACE INTO boite56106_operations(compte, cle, empreinte, reponse, quand) VALUES(?,?,?,?,?)',
                    (owner, cle, _empreinte(corps), json.dumps(reponse, ensure_ascii=False), _now()))
    desk.db.commit()


# ------------------------------------------------------------------------------------------------ actions groupées
def _ensemble(uids):
    """Ensemble d'UID IMAP compact : suites consécutives en plages (« 3:9 »), sinon séparées par des virgules."""
    tri, out = sorted({int(u) for u in uids}), []
    for u in tri:
        if out and out[-1][1] == u - 1:
            out[-1][1] = u
        else:
            out.append([u, u])
    return ','.join(str(a) if a == b else '%d:%d' % (a, b) for a, b in out)


def _uids(items):
    out = []
    for x in items:
        u = str(x.get('uid') if isinstance(x, dict) else x)
        if not re.fullmatch(r'\d{1,12}', u):
            raise Stop('introuvable')
        out.append(u)
    return list(dict.fromkeys(out))


def _autorises(desk, acteur, box, cfg, dossier, validite, uids):
    """UID que l'acteur peut voir (droits relus maintenant). Brouillons d'un compte cloisonné : origine de chacun contrôlée (``exiger``).
    Panne du contrôle : ``controle_indisponible`` (rien n'est fait)."""
    from . import acces_courriel56100 as ac
    from .messages5697 import cle_canonique
    caches = ac.masques(desk, acteur)
    if not caches:
        return list(uids)
    if dossier == cfg.get('drafts'):
        gardes = []
        for u in uids:
            try:
                ac.exiger(desk, acteur, {'genre': 'imap', 'boite': dossier, 'uid': u, 'uidvalidity': validite}, box=box, cfg=cfg)
                gardes.append(u)
            except Stop as ex:
                if str(ex) != ac.NEUTRE:
                    raise
        from .drafts440 import _select
        if str(_select(box, dossier, False)) != str(validite):   # ``exiger`` a resélectionné en lecture : retour en écriture
            raise Stop('boite_reindexee')
        return gardes
    compte = _compte(cfg)
    cles = {u: cle_canonique(compte, dossier, validite, u) for u in uids}
    rattachements = ac.dossiers(desk, list(cles.values()))
    return [u for u in uids if ac._visible(rattachements.get(cles[u], ()), caches)]


def _presents(box, uids):
    if not uids:
        return []
    status, data = box.conn.uid('SEARCH', None, 'UID', _ensemble(uids), 'UNDELETED')
    if status != 'OK':
        raise Stop('recherche_imap_echouee')
    vus = set(data[0].decode().split()) if data and data[0] else set()
    return [u for u in uids if u in vus]


def _drapeaux(box, uids):
    status, data = box.conn.uid('FETCH', _ensemble(uids), '(UID FLAGS)')
    if status != 'OK':
        raise Stop('lecture_imap_echouee')
    import imaplib
    out = {}
    for x in data or ():
        meta = x[0] if isinstance(x, tuple) else x
        if not isinstance(meta, bytes):
            continue
        m = re.search(rb'UID (\d+)', meta)
        if m:
            out[m[1].decode()] = {f.decode() for f in imaplib.ParseFlags(meta)}
    return out


def _cible(desk, box, cfg, dossier, action, data, acteur, owner):
    from .webmail5628 import _archive_folder, _role_folder
    from .boite5625 import _folder
    if action == 'archive':
        return _archive_folder(box, cfg)
    if action == 'junk':
        dest = _role_folder(box, cfg, 'junk')
        if not dest:
            raise Stop('dossier_indesirables_absent')
        return dest
    if action == 'not_junk':
        return cfg['inbox']
    if action == 'trash':
        dest = _role_folder(box, cfg, 'trash')
        if not dest:
            from .drafts440 import find_trash
            dest = find_trash(box)
        if not dest:
            raise Stop('corbeille_absente')
        if dest == dossier:
            raise Stop('deja_dans_la_corbeille_suppression_definitive_dans_la_messagerie')
        return dest
    if action == 'move':
        if not data.get('cible'):
            raise Stop('cible_a_choisir')
        return _folder(box, cfg, data.get('cible'))
    if action == 'sous_dossier':
        return sous_dossier(desk, acteur, owner, str(data.get('matter') or ''), box=box)['dossier']
    raise Stop('action_inconnue')


def _deplacer(box, uids, cible):
    """Une seule commande pour l'ensemble : UID MOVE (RFC 6851), sinon UID COPY puis STORE \\Deleted puis UID EXPUNGE (UIDPLUS)."""
    from .webmail5628 import _caps
    ensemble = _ensemble(uids)
    quoted = mbx.imap_quote(mbx.modified_utf7(cible))
    caps = _caps(box)
    if 'MOVE' in caps:
        status, _ = box.conn.uid('MOVE', ensemble, quoted)
        return status == 'OK'
    status, _ = box.conn.uid('COPY', ensemble, quoted)
    if status != 'OK':
        return False
    status, _ = box.conn.uid('STORE', ensemble, '+FLAGS.SILENT', '(\\Deleted)')
    if status == 'OK' and 'UIDPLUS' in caps:
        box.conn.uid('EXPUNGE', ensemble)
    return status == 'OK'


def _phrase(action, reussis, echecs):
    sing, plur = VERBES[action]
    texte = '%d %s' % (reussis, sing if reussis == 1 else plur) if reussis else 'Aucun courriel traité'
    if echecs:
        motifs = {}
        for e in echecs:
            motifs[e['motif']] = motifs.get(e['motif'], 0) + 1
        detail = ', '.join(('%s' % m) if len(motifs) == 1 else '%d %s' % (n, m) for m, n in motifs.items())
        texte += ', %d en échec (%s)' % (len(echecs), detail)
    return texte + '.'


def actions(desk, acteur, data, owner='cabinet'):
    """Action groupée : ``{'action', 'dossier', 'uidvalidity', 'items': [{uid}], 'cible'?, 'matter'?, 'cle'}``. Rend le bilan exact."""
    from . import pause5629
    from .boite5625 import _folder
    from .drafts440 import _select
    action = str(data.get('action') or '')
    if action not in ACTIONS:
        raise Stop('action_inconnue')
    items = data.get('items') if isinstance(data.get('items'), list) else []
    if not items:
        raise Stop('aucun_courriel')
    if len(items) > (MAX_CLASSER if action == 'classer' else MAX_ACTIONS):
        raise Stop('trop_de_courriels_a_classer' if action == 'classer' else 'trop_de_courriels')
    uids = _uids(items)
    corps = {'action': action, 'dossier': str(data.get('dossier') or ''), 'uidvalidity': str(data.get('uidvalidity') or ''), 'uids': uids,
             'cible': str(data.get('cible') or ''), 'matter': str(data.get('matter') or '')}
    cle = str(data.get('cle') or '')
    deja = _recu(desk, owner, cle, corps)
    if deja is not None:
        return deja
    cfg = desk.c['mail']
    box = mbx.Mailbox(cfg)
    try:
        dossier = _folder(box, cfg, data.get('dossier') or None)
        if action == 'not_junk' and dossier == cfg['inbox']:
            raise Stop('courriel_deja_dans_ce_dossier')
        validite = str(_select(box, dossier, False))          # sélection en écriture
        if corps['uidvalidity'] and corps['uidvalidity'] != validite:
            raise Stop('boite_reindexee')                       # rien n'est fait : la liste affichée ne désigne plus ces messages
        autorises = set(_autorises(desk, acteur, box, cfg, dossier, validite, uids))
        presents = set(_presents(box, [u for u in uids if u in autorises]))
        echecs = [{'uid': u, 'motif': MOTIFS['absent']} for u in uids if u not in presents]   # interdit = absent (motif neutre)
        cibles = [u for u in uids if u in presents]
        reussis, cible = [], ''
        if cibles and action in ('epingler', 'desepingler'):   # propre à AxiorHub : aucun drapeau IMAP, rien n'est écrit dans la messagerie
            reussis = _epingler(desk, owner, box, cfg, dossier, validite, cibles, action == 'epingler')
            echecs += [{'uid': u, 'motif': MOTIFS['absent']} for u in cibles if u not in reussis]
        elif cibles and action == 'classer':
            reussis, autres = _classer(desk, acteur, owner, box, cfg, dossier, validite, cibles, str(data.get('matter') or ''), cle)
            echecs += autres
        elif cibles and action in DRAPEAUX:
            signe, drapeau = DRAPEAUX[action]
            pause5629.checkpoint(desk)
            status, _ = box.conn.uid('STORE', _ensemble(cibles), signe + 'FLAGS.SILENT', '(%s)' % drapeau)
            constates = _drapeaux(box, cibles) if status == 'OK' else {}
            for u in cibles:
                ok = u in constates and ((drapeau in constates[u]) == (signe == '+'))
                (reussis.append(u) if ok else echecs.append({'uid': u, 'motif': MOTIFS['refuse' if status != 'OK' else 'non_constate']}))
        elif cibles:
            cible = _cible(desk, box, cfg, dossier, action, data, acteur, owner)
            if cible == dossier:
                raise Stop('courriel_deja_dans_ce_dossier')
            _select(box, dossier, False)                        # la cible a pu être créée ou listée entre-temps : sélection rétablie
            if str(box.validity) != validite:
                raise Stop('boite_reindexee')
            pause5629.checkpoint(desk)
            accepte = _deplacer(box, cibles, cible)
            restants = set(_presents(box, cibles))              # constat : un message encore là n'a pas été déplacé
            for u in cibles:
                if u in restants:
                    echecs.append({'uid': u, 'motif': MOTIFS['refuse' if not accepte else 'non_constate']})
                else:
                    reussis.append(u)
            if reussis:
                _suivre_deplacement(desk, cfg, dossier, reussis, cible)
        out = {'action': action, 'dossier': dossier, 'reussis': len(reussis), 'uids_reussis': reussis, 'echecs': echecs,
               'message': _phrase(action, len(reussis), echecs)}
        if cible:
            out['cible'] = cible
        desk.audit('boite56106_actions', {'action': action, 'demandes': len(uids), 'reussis': len(reussis), 'echecs': len(echecs)})
        _garder(desk, owner, cle, corps, out)
        return out
    finally:
        box.close()


def _classer(desk, acteur, owner, box, cfg, dossier, validite, uids, matter, cle):
    """Classement dans le dossier client par la conservation existante (``correspondances5643`` : proposition puis confirmation, courriel
    conservé dans CORRESPONDANCES). Le dossier client choisi est contrôlé pour l'acteur ; chaque message l'a été avant l'appel."""
    from . import correspondances5643 as c43, pause5629
    from .acces_courriel56100 import exiger_contexte
    if not matter:
        raise Stop('dossier_a_choisir')
    exiger_contexte(desk, acteur, '', matter)
    if matter not in {m['id'] for m in load_matters(desk.c)}:
        raise Stop('introuvable')
    reussis, echecs = [], []
    for u in uids:
        pause5629.checkpoint(desk)
        try:
            prop = c43.prepare(desk, owner, {'actions': ['archive_email'], 'matter_id': matter,
                                             'source': {'folder': dossier, 'uid': u, 'uidvalidity': validite}}, box=box)
            c43.confirm(desk, owner, {'proposal_id': prop['proposal_id'], 'expected_version': prop['version'],
                                      'request_key': ('l106-%s-%s' % (cle, u))[:80]})
            reussis.append(u)
        except Stop as ex:
            echecs.append({'uid': u, 'motif': MESSAGES.get(str(ex), str(ex).replace('_', ' '))[:120]})
    return reussis, echecs


# ------------------------------------------------------------------------------------------------ épingles (propres à AxiorHub)
def cle_epingle(compte, message_id, dossier, uidvalidity, uid):
    """Clé stable d'une épingle : compte + Message-ID (survit à un déplacement) ; à défaut compte + dossier + UIDVALIDITY + UID."""
    if message_id:
        return 'mid:' + hashlib.sha256(('%s|%s' % (compte, str(message_id).strip().lower())).encode()).hexdigest()[:40]
    return 'uid:' + hashlib.sha256(('%s|%s|%s|%s' % (compte, dossier, uidvalidity, uid)).encode()).hexdigest()[:40]


def _ou(termes):
    """Critères IMAP combinés par OR (préfixe, RFC 3501 § 6.4.4) : [[A..], [B..], [C..]] → OR A OR B C."""
    if len(termes) == 1:
        return list(termes[0])
    return ['OR'] + list(termes[0]) + _ou(termes[1:])


def _par_message_id(box, mids):
    """UID (sélection courante) des messages portant ces Message-ID : une seule recherche (OR), en-têtes seulement."""
    mids = [m for m in dict.fromkeys(mids) if m and m.isascii()][:MAX_EPINGLES]
    if not mids:
        return {}
    status, data = box.conn.uid('SEARCH', None, 'UNDELETED', *_ou([['HEADER', 'Message-ID', mbx.imap_quote(m)] for m in mids]))
    if status != 'OK':
        raise Stop('recherche_imap_echouee')
    uids = data[0].decode().split() if data and data[0] else []
    if not uids:
        return {}
    trouves = box.entetes(box.selected, uids, expected_validity=box.validity)
    return {m.mid.lower(): u for u, m in trouves.items() if m.mid}


def epingles_du_dossier(desk, box, owner, dossier, validite, autorises):
    """UID épinglés par cette personne présents dans le dossier affiché (dans l'ordre : la plus récente épingle d'abord), parmi les UID
    ``autorises`` (droits et filtres déjà appliqués). Lecture seule : une épingle dont le message a disparu n'apparaît simplement pas."""
    try:
        rows = desk.db.execute('SELECT message_id, dossier, uidvalidity, uid FROM boite56106_epingles WHERE proprietaire=? AND compte_imap=? '
                               'ORDER BY cree_le DESC LIMIT ?', (str(owner), _compte(desk.c['mail']), MAX_EPINGLES)).fetchall()
    except Exception:   # noqa: BLE001 — table absente (démarrage incomplet) : aucune épingle, rien n'est écrit pendant la lecture
        return []
    if not rows:
        return []
    permis = set(str(u) for u in autorises)
    par_mid = _par_message_id(box, [r['message_id'] for r in rows if r['message_id']])
    out = []
    for r in rows:
        u = par_mid.get(r['message_id'].lower()) if r['message_id'] else (r['uid'] if (r['dossier'] == dossier and r['uidvalidity'] == str(validite)) else '')
        if u and u in permis and u not in out:
            out.append(u)
    return out


def _epingler(desk, owner, box, cfg, dossier, validite, uids, epingle):
    """Épingle ou désépingle ces messages (présents et autorisés) ; plafond de ``MAX_EPINGLES`` par personne. Aucun drapeau IMAP."""
    compte = _compte(cfg)
    mails = box.entetes(dossier, uids, expected_validity=validite) if uids else {}
    faits = []
    for u in uids:
        m = mails.get(str(u))
        if m is None:
            continue
        cles = {cle_epingle(compte, m.mid, dossier, validite, u), cle_epingle(compte, '', dossier, validite, u)}
        if epingle:
            nb = desk.db.execute('SELECT COUNT(*) FROM boite56106_epingles WHERE proprietaire=? AND compte_imap=?', (owner, compte)).fetchone()[0]
            deja = desk.db.execute('SELECT 1 FROM boite56106_epingles WHERE proprietaire=? AND compte_imap=? AND cle=?',
                                   (owner, compte, cle_epingle(compte, m.mid, dossier, validite, u))).fetchone()
            if not deja and nb >= MAX_EPINGLES:
                raise Stop('trop_d_epingles')
            desk.db.execute('INSERT OR REPLACE INTO boite56106_epingles(proprietaire, compte_imap, cle, message_id, dossier, uidvalidity, uid, cree_le) '
                            'VALUES(?,?,?,?,?,?,?,?)', (owner, compte, cle_epingle(compte, m.mid, dossier, validite, u), m.mid, dossier, str(validite), str(u), _now()))
        else:
            desk.db.execute('DELETE FROM boite56106_epingles WHERE proprietaire=? AND compte_imap=? AND cle IN (%s)' % ','.join('?' * len(cles)),
                            (owner, compte, *sorted(cles)))
        faits.append(u)
    desk.db.commit()
    return faits


def _suivre_deplacement(desk, cfg, source, uids, cible):
    """Après un déplacement par AxiorHub : l'épingle (Message-ID) suit le message ; son UID d'origine n'a plus de sens."""
    try:
        desk.db.execute('UPDATE boite56106_epingles SET dossier=?, uid=\'\', uidvalidity=\'\' WHERE compte_imap=? AND dossier=? AND uid IN (%s)'
                        % ','.join('?' * len(uids)), (cible, _compte(cfg), source, *uids))
        desk.db.commit()
    except Exception:   # noqa: BLE001 — l'épingle est une commodité : un échec ne change pas le bilan du déplacement
        pass


# ------------------------------------------------------------------------------------------------ « Rechercher dans »
def ou_champs(terme, champs):
    """Critères ASCII de la recherche simple combinés par OR : Objet (SUBJECT), Corps (BODY), Expéditeur (FROM), Destinataires (TO, CC)."""
    q = mbx.imap_quote(terme)
    termes = []
    for c in champs:
        termes += {'objet': [['SUBJECT', q]], 'corps': [['BODY', q]], 'expediteur': [['FROM', q]], 'destinataires': [['TO', q], ['CC', q]]}.get(c, [])
    return tuple(_ou(termes or [['SUBJECT', q], ['FROM', q]]))


def champs_recherche(valeur):
    vus = [c for c in str(valeur or '').split(',') if c in CHAMPS_RECHERCHE]
    return tuple(c for c in CHAMPS_RECHERCHE if c in vus) or RECHERCHE_DEFAUT


def preference_recherche(desk, owner):
    try:
        valeur = desk.settings('boite56106:recherche:' + str(owner))
    except Exception:   # noqa: BLE001 — réglage illisible : comme aujourd'hui (objet et expéditeur)
        valeur = None
    return list(champs_recherche(','.join(valeur))) if isinstance(valeur, list) else list(RECHERCHE_DEFAUT)


def regler_recherche(desk, owner, champs):
    choisis = [c for c in CHAMPS_RECHERCHE if c in (champs if isinstance(champs, list) else [])]
    if not choisis:
        raise Stop('champs_de_recherche_a_choisir')
    desk.setting('boite56106:recherche:' + str(owner), choisis)
    return {'recherche': choisis, 'message': 'Recherche dans : %s.' % ', '.join({'objet': 'objet', 'corps': 'corps', 'expediteur': 'expéditeur',
                                                                                  'destinataires': 'destinataires'}[c] for c in choisis)}


# ------------------------------------------------------------------------------------------------ « Tout le dossier comme lu »
def tout_lu(desk, acteur, data, owner='cabinet'):
    """Marque lus tous les courriels non lus (et autorisés) du dossier, après confirmation de leur nombre ; effet constaté au serveur."""
    from . import pause5629
    from .boite5625 import _folder
    from .drafts440 import _select
    corps = {'op': 'tout-lu', 'dossier': str(data.get('dossier') or ''), 'uidvalidity': str(data.get('uidvalidity') or ''),
             'confirme': str(data.get('confirme') or '')}
    deja = _recu(desk, owner, str(data.get('cle') or ''), corps)
    if deja is not None:
        return deja
    cfg = desk.c['mail']
    box = mbx.Mailbox(cfg)
    try:
        dossier = _folder(box, cfg, corps['dossier'] or None)
        validite = str(_select(box, dossier, False))
        if corps['uidvalidity'] and corps['uidvalidity'] != validite:
            raise Stop('boite_reindexee')
        status, res = box.conn.uid('SEARCH', None, 'UNSEEN', 'UNDELETED')
        uids = res[0].decode().split() if status == 'OK' and res and res[0] else []
        uids = _autorises(desk, acteur, box, cfg, dossier, validite, uids)
        if len(uids) > MAX_TOUT_LU:
            raise Stop('trop_de_courriels_non_lus')
        if not uids:
            out = {'reussis': 0, 'echecs': [], 'message': 'Aucun courriel non lu dans ce dossier.'}
        else:
            if corps['confirme'] != str(len(uids)):
                return {'confirmation_requise': True, 'nombre': len(uids),   # rien n'est fait : le nombre est confirmé par l'avocat
                        'message': 'Marquer comme lus les %d courriel(s) non lus de ce dossier ? Confirmez.' % len(uids)}
            for debut in range(0, len(uids), 1000):
                pause5629.checkpoint(desk)
                box.conn.uid('STORE', _ensemble(uids[debut:debut + 1000]), '+FLAGS.SILENT', '(\\Seen)')
            status, res = box.conn.uid('SEARCH', None, 'UNSEEN', 'UNDELETED')   # constat : ceux qui restent non lus
            restants = set(res[0].decode().split()) if status == 'OK' and res and res[0] else set()
            reussis = [u for u in uids if u not in restants]
            echecs = [{'uid': u, 'motif': MOTIFS['non_constate']} for u in uids if u in restants]
            out = {'reussis': len(reussis), 'echecs': echecs, 'message': _phrase('lu', len(reussis), echecs)}
        desk.audit('boite56106_tout_lu', {'reussis': out['reussis']})
        _garder(desk, owner, data['cle'], corps, out)
        return out
    finally:
        box.close()


# ------------------------------------------------------------------------------------------------ dossiers
def _verifier_existe(box, nom):
    return any(x['name'] == nom for x in lister_imap(box))


def creer_dossier(desk, data, owner='cabinet', box=None):
    """Nouveau dossier (sous ``parent`` facultatif, jamais sous un dossier spécial sauf la Boîte de réception de l'espace personnel)."""
    from .boite5625 import _folder
    cfg = desk.c['mail']
    corps = {'op': 'creer', 'nom': str(data.get('nom') or ''), 'parent': str(data.get('parent') or '')}
    deja = _recu(desk, owner, str(data.get('cle') or ''), corps)
    if deja is not None:
        return deja
    own = box is None
    box = box or mbx.Mailbox(cfg)
    try:
        lignes = lister_imap(box)
        sep = separateur(lignes)
        nom = nom_sur(corps['nom'], sep)
        if not nom:
            raise Stop('nom_de_dossier_vide')
        parent = _folder(box, cfg, corps['parent']) if corps['parent'] else prefixe_personnel(lignes, sep).rstrip(sep)
        role = _roles(box, cfg).get(parent, '') if corps['parent'] else ''
        if role in SPECIAUX and role != 'inbox':
            raise Stop('dossier_special_protege')
        complet = nom_libre([x['name'] for x in lignes], parent, nom, sep)
        out = _creer(desk, box, complet)
        out['message'] = 'Dossier « %s » créé.' % complet.rsplit(sep, 1)[-1]
        desk.audit('boite56106_dossier_cree', {'profondeur': complet.count(sep)})
        _garder(desk, owner, data['cle'], corps, out)
        return out
    finally:
        if own:
            box.close()


def _creer(desk, box, complet):
    from . import pause5629
    pause5629.checkpoint(desk)
    status, _ = box.conn.create(mbx.imap_quote(mbx.modified_utf7(complet)))
    if status != 'OK' or not _verifier_existe(box, complet):   # constat au serveur : jamais de succès sans preuve
        raise Stop('creation_dossier_refusee')
    try:
        box.conn.subscribe(mbx.imap_quote(mbx.modified_utf7(complet)))
    except Exception:   # noqa: BLE001 — abonnement facultatif (le dossier existe)
        pass
    return {'dossier': complet}


def renommer_dossier(desk, data, owner='cabinet', box=None):
    from .boite5625 import _folder
    cfg = desk.c['mail']
    corps = {'op': 'renommer', 'ancien': str(data.get('ancien') or ''), 'nom': str(data.get('nom') or '')}
    deja = _recu(desk, owner, str(data.get('cle') or ''), corps)
    if deja is not None:
        return deja
    own = box is None
    box = box or mbx.Mailbox(cfg)
    try:
        ancien = _folder(box, cfg, corps['ancien'] or '\x00')
        lignes = lister_imap(box)
        sep = separateur(lignes)
        if _protege(box, cfg, ancien, lignes, sep):
            raise Stop('dossier_special_protege')
        nom = nom_sur(corps['nom'], sep)
        if not nom:
            raise Stop('nom_de_dossier_vide')
        parent = ancien.rsplit(sep, 1)[0] if sep in ancien else ''
        nouveau = nom_libre([x['name'] for x in lignes if x['name'] != ancien], parent, nom, sep)
        if nouveau == ancien:
            return {'dossier': ancien, 'message': 'Nom inchangé.'}
        from . import pause5629
        pause5629.checkpoint(desk)
        status, _ = box.conn.rename(mbx.imap_quote(mbx.modified_utf7(ancien)), mbx.imap_quote(mbx.modified_utf7(nouveau)))
        if status != 'OK' or not _verifier_existe(box, nouveau):
            raise Stop('renommage_refuse')
        compte = _compte(cfg)
        for row in desk.db.execute('SELECT matter_id, nom_imap FROM boite56106_sous_dossiers WHERE compte_imap=?', (compte,)).fetchall():
            if row['nom_imap'] == ancien or row['nom_imap'].startswith(ancien + sep):
                desk.db.execute('UPDATE boite56106_sous_dossiers SET nom_imap=? WHERE matter_id=? AND compte_imap=?',
                                (nouveau + row['nom_imap'][len(ancien):], row['matter_id'], compte))
        desk.db.commit()
        out = {'dossier': nouveau, 'ancien': ancien, 'message': 'Dossier renommé en « %s ».' % nouveau.rsplit(sep, 1)[-1]}
        desk.audit('boite56106_dossier_renomme', {})
        _garder(desk, owner, data['cle'], corps, out)
        return out
    finally:
        if own:
            box.close()


def supprimer_dossier(desk, acteur, data, owner='cabinet', box=None):
    """Suppression d'un dossier : un dossier non vide n'est supprimé que si ``confirme`` porte le nombre de courriels affiché ; ses
    courriels partent d'abord dans la Corbeille (récupérables), puis la suppression est constatée au serveur."""
    from .boite5625 import _folder
    from .drafts440 import _select
    cfg = desk.c['mail']
    corps = {'op': 'supprimer', 'dossier': str(data.get('dossier') or ''), 'confirme': str(data.get('confirme') or '')}
    deja = _recu(desk, owner, str(data.get('cle') or ''), corps)
    if deja is not None:
        return deja
    own = box is None
    box = box or mbx.Mailbox(cfg)
    try:
        nom = _folder(box, cfg, corps['dossier'] or '\x00')
        lignes = lister_imap(box)
        sep = separateur(lignes)
        if _protege(box, cfg, nom, lignes, sep):
            raise Stop('dossier_special_protege')
        if any(x['name'].startswith(nom + sep) for x in lignes):
            raise Stop('dossier_avec_sous_dossiers')
        validite = str(_select(box, nom, False))
        status, data_ = box.conn.uid('SEARCH', None, 'UNDELETED')
        uids = data_[0].decode().split() if status == 'OK' and data_ and data_[0] else []
        if uids:
            if len(_autorises(desk, acteur, box, cfg, nom, validite, uids)) != len(uids):   # avant tout nombre affiché
                raise Stop('dossier_contient_courriels_reserves')
            if corps['confirme'] != str(len(uids)):
                return {'confirmation_requise': True, 'nombre': len(uids), 'dossier': nom,   # rien n'est fait : le nombre est confirmé par l'avocat
                        'message': 'Ce dossier contient %d courriel(s) : ils seront déplacés dans la Corbeille (récupérables), puis le dossier sera supprimé. Confirmez.' % len(uids)}
            corbeille = _cible(desk, box, cfg, nom, 'trash', {}, acteur, owner)
            _select(box, nom, False)
            from . import pause5629
            pause5629.checkpoint(desk)
            if not _deplacer(box, uids, corbeille) or _presents(box, uids):
                raise Stop('suppression_refusee')
        from . import pause5629
        pause5629.checkpoint(desk)
        box.conn.select(mbx.imap_quote(mbx.modified_utf7(cfg['inbox'])), readonly=True)   # le dossier à supprimer n'est plus sélectionné
        status, _ = box.conn.delete(mbx.imap_quote(mbx.modified_utf7(nom)))
        if status != 'OK' or _verifier_existe(box, nom):
            raise Stop('suppression_refusee')
        desk.db.execute('DELETE FROM boite56106_sous_dossiers WHERE compte_imap=? AND nom_imap=?', (_compte(cfg), nom))
        desk.db.commit()
        out = {'dossier': nom, 'deplaces': len(uids),
               'message': 'Dossier supprimé.' + (' %d courriel(s) déplacé(s) dans la Corbeille.' % len(uids) if uids else '')}
        desk.audit('boite56106_dossier_supprime', {'courriels': len(uids)})
        _garder(desk, owner, data['cle'], corps, out)
        return out
    finally:
        if own:
            box.close()


def _matiere(desk, matter):
    return next((m for m in load_matters(desk.c) if m['id'] == matter), None)


def lien_sous_dossier(desk, acteur, matter):
    """Sous-dossier mémorisé pour ce dossier client (lecture seule ; aucun accès à la messagerie)."""
    from .acces_courriel56100 import exiger_contexte
    exiger_contexte(desk, acteur, '', matter)
    if not _matiere(desk, matter):
        raise Stop('introuvable')
    row = desk.db.execute('SELECT nom_imap FROM boite56106_sous_dossiers WHERE matter_id=? AND compte_imap=?',
                          (matter, _compte(desk.c['mail']))).fetchone()
    return {'matter': matter, 'dossier': row['nom_imap'] if row else ''}


def sous_dossier(desk, acteur, owner, matter, box=None):
    """Sous-dossier de messagerie du dossier client, sous « Dossiers clients » : le lien mémorisé s'il existe encore au serveur, sinon
    créé (parent créé au besoin) et mémorisé. Le dossier client doit être visible de l'acteur."""
    from .acces_courriel56100 import exiger_contexte
    if not matter:
        raise Stop('dossier_a_choisir')
    exiger_contexte(desk, acteur, '', matter)
    m = _matiere(desk, matter)
    if not m:
        raise Stop('introuvable')
    cfg = desk.c['mail']
    compte = _compte(cfg)
    own = box is None
    box = box or mbx.Mailbox(cfg)
    try:
        lignes = lister_imap(box)
        noms = [x['name'] for x in lignes]
        row = desk.db.execute('SELECT nom_imap FROM boite56106_sous_dossiers WHERE matter_id=? AND compte_imap=?', (matter, compte)).fetchone()
        if row and row['nom_imap'] in noms:
            return {'dossier': row['nom_imap'], 'cree': False, 'matter': matter}
        sep = separateur(lignes)
        parent = prefixe_personnel(lignes, sep) + PARENT_CLIENTS
        if parent not in noms:
            _creer(desk, box, parent)
            noms.append(parent)
        libelle = nom_sur(' - '.join(x for x in (str(m.get('client_name') or ''), str(m['id'])) if x), sep) or nom_sur(m['id'], sep) or 'Dossier'
        complet = nom_libre(noms, parent, libelle, sep)
        _creer(desk, box, complet)
        desk.db.execute('INSERT OR REPLACE INTO boite56106_sous_dossiers(matter_id, compte_imap, nom_imap, cree_par, cree_le) VALUES(?,?,?,?,?)',
                        (matter, compte, complet, owner, _now()))
        desk.db.commit()
        desk.audit('boite56106_sous_dossier_cree', {'matter': matter})
        return {'dossier': complet, 'cree': True, 'matter': matter}
    finally:
        if own:
            box.close()


# ------------------------------------------------------------------------------------------------ page
def monter(body, head, prefix, desk, owner):
    """Point de montage de la page Courriels (nouvelle interface) : classe posée sur la section, script du lot ajouté. La densité suit le
    réglage commun du lot D (``body[data-densite]``, compacte par défaut) : aucun réglage propre à Courriels."""
    from html import escape
    body = body.replace('<section class="bx5625" id="bx5625"', '<section class="bx5625 bx56106" id="bx5625"', 1)
    return body, head + '<script defer src="%s"></script>' % escape(prefix + '/static/v56106-liste.js', quote=True)


def bloc_dossier(desk, prefix, matter):
    """Fiche du dossier (onglet Échanges) : bouton « Sous-dossier de messagerie » (créé au besoin sous « Dossiers clients »). Aucune lecture
    IMAP au rendu : l'état est lu par le script (route GET sans écriture). Messagerie absente ou rôle sans messagerie : rien n'est affiché."""
    from html import escape
    from .membres5650 import viewer_role
    mail = desk.c.get('mail') or {}
    if mail.get('enabled', True) is False or not mail.get('host') or viewer_role() not in ROLES:
        return ''
    mid = escape(str(matter.get('id') or ''), quote=True)
    return ('<section class="d97-section bx56106-fiche"><h2>Messagerie</h2><p><button type="button" class="ax-btn ghost" data-bx56106-sous-dossier="%s">'
            '📂 Créer le sous-dossier de messagerie</button> <span class="ax-muted" role="status" data-bx56106-sous-dossier-etat></span></p>'
            '<p class="ax-muted">Sous « Dossiers clients » dans la messagerie ; proposé ensuite dans Courriels pour y déplacer les courriels du dossier.</p>'
            '<script defer src="%s"></script></section>') % (mid, escape(prefix + '/static/v56106-liste.js', quote=True))


# ------------------------------------------------------------------------------------------------ API
def allowed(role, method, api):
    reste = str(api or '').split('?', 1)[0][len(PREFIXE):].strip('/')
    if method in ('GET', 'HEAD'):
        return reste in ROUTES_GET and role in ROLES
    return method == 'POST' and reste in ROUTES_POST and role in ROLES


def _prive(out):
    out['headers'] = [h for h in out.get('headers', []) if h[0].lower() != 'cache-control'] + [('Cache-Control', 'private, no-store')]
    return out


def message(code):
    if code in MESSAGES:
        return MESSAGES[code]
    from .web440 import human
    return human(code)


def api(env, desk, auth, prefix, name, args, method):
    from .web440 import check_post, json_out
    from .web567 import actor
    owner, role = actor(env)
    reste = str(name).split('?', 1)[0][len(PREFIXE):].strip('/')
    try:
        if reste not in ROUTES_GET + ROUTES_POST:
            raise Stop('route_inconnue')
        if role not in ROLES:
            raise Stop('role_insuffisant')
        acteur = (owner, role)
        if method in ('GET', 'HEAD'):
            if reste == 'dossiers':   # avec le choix « Rechercher dans » de la personne (lecture seule)
                return _prive(json_out(dict(dossiers(desk), recherche=preference_recherche(desk, owner))))
            if reste == 'sous-dossier':
                return _prive(json_out(lien_sous_dossier(desk, acteur, str(args.get('matter') or ''))))
            raise Stop('route_inconnue')
        if method != 'POST':
            raise Stop('route_inconnue')
        if int(env.get('CONTENT_LENGTH') or 0) > MAX_POST:
            raise Stop('requete_trop_longue')
        data = check_post(env, auth)
        if reste == 'actions':
            return _prive(json_out(actions(desk, acteur, data, owner)))
        if reste == 'dossier/creer':
            return _prive(json_out(creer_dossier(desk, data, owner)))
        if reste == 'dossier/renommer':
            return _prive(json_out(renommer_dossier(desk, data, owner)))
        if reste == 'dossier/supprimer':
            return _prive(json_out(supprimer_dossier(desk, acteur, data, owner)))
        if reste == 'dossier/tout-lu':
            return _prive(json_out(tout_lu(desk, acteur, data, owner)))
        if reste == 'preferences':
            return _prive(json_out(regler_recherche(desk, owner, data.get('recherche'))))
        if reste == 'sous-dossier':
            corps = {'op': 'sous-dossier', 'matter': str(data.get('matter') or '')}
            deja = _recu(desk, owner, str(data.get('cle') or ''), corps)
            if deja is not None:
                return _prive(json_out(deja))
            out = sous_dossier(desk, acteur, owner, corps['matter'])
            out['message'] = ('Sous-dossier « %s » créé.' if out['cree'] else 'Sous-dossier « %s » déjà présent.') % out['dossier']
            _garder(desk, owner, data['cle'], corps, out)
            return _prive(json_out(out))
        raise Stop('route_inconnue')
    except Stop as ex:
        code = str(ex).split(':')[0]
        corps = {'error': code, 'message': message(code)}
        return _prive(json_out(corps, STATUTS.get(code, '400 Bad Request')))
