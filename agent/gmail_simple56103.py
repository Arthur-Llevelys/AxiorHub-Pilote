"""5.6.103 (lot G) : Gmail simple — IMAP avec un mot de passe d'application (chemin par défaut) et assistant pas à pas pour une application
OAuth « Interne » de Google Workspace créée par le cabinet dans sa propre console.

Décision du cabinet (15/11/2026) : aucun secret OAuth Google n'est embarqué dans AxiorHub (dépôt public). Les autorisations ``gmail.readonly``
et ``gmail.compose`` sont « restreintes » chez Google : une application ouverte à des comptes externes demande la validation de Google et une
évaluation de sécurité ; en mode test, 100 utilisateurs au plus et des autorisations qui expirent après 7 jours. D'où deux chemins :

1. **IMAP avec mot de passe d'application** (par défaut), sur le moteur IMAP existant (``mailbox``) : lecture, brouillons avec pièces jointes
   déposés dans les Brouillons du compte par l'action distincte habituelle (« Déposer dans Brouillons ») ; rien n'est jamais envoyé.
   - Préréglage : ``imap.gmail.com``, port 993, TLS direct, boîte de réception ``INBOX``.
   - Les dossiers spéciaux sont reconnus par leur **attribut** (RFC 6154, annoncé par Gmail dans chaque réponse LIST : ``\\Drafts``,
     ``\\Sent``, ``\\Trash``, ``\\Junk``, ``\\All``), jamais par leur nom : ``[Gmail]/Brouillons``, ``[Gmail]/Drafts``, ``[Google Mail]/Entwürfe``…
   - Test de connexion à la demande (administrateur) : connexion, LIST, ouverture en lecture seule des Brouillons ; résultat noté
     (``gmail_simple56103_essais``), succès comme échec, pour que l'assistant affiche l'état réel.
   - Refus de Google traduit en message clair (mot de passe habituel au lieu d'un mot de passe d'application, IMAP coupé par
     l'administrateur Workspace, vérification demandée dans le navigateur, connexions trop nombreuses).
   - Le mot de passe d'application n'est jamais écrit dans la configuration, un journal, une réponse ni la base : il sert à la connexion de
     test puis, à l'enregistrement, il est rangé par ``config567`` dans le coffre (``vault567``), comme tout autre secret de Paramètres.
2. **Application OAuth interne Workspace** sur le connecteur Gmail existant (``google5697`` / ``gmail5697``) : étapes exactes dans la console
   Google Cloud, champs à remplir, et formulaire où coller l'identifiant client et le secret. Le secret part dans le coffre du serveur
   (clé ``google5697.client_secret_file`` de ``config567``) et n'est plus jamais affiché, ni en entier, ni en partie.

Écritures : administrateur seulement (la messagerie IMAP et le client OAuth sont des réglages du cabinet), CSRF, clé d'idempotence, révision
de la configuration (409 si un autre écran l'a modifiée). Aucune écriture pendant une lecture ; le schéma est créé au démarrage (``Desk``).
"""
from datetime import datetime, timezone
from html import escape as e
import hashlib
import imaplib
import json
from pathlib import Path
import re
from urllib.parse import urlsplit
import uuid

from .common import Stop
from . import mailbox as mbx   # référence au module : la connexion est remplaçable par les essais (faux serveur IMAP)

PRESET = {'host': 'imap.gmail.com', 'port': 993, 'security': 'ssl', 'inbox': 'INBOX'}
HOTES_GMAIL = ('imap.gmail.com', 'imap.googlemail.com')
DELAI_TEST = 20                       # secondes : connexion de test bornée
ATTRIBUTS = {'\\drafts': 'drafts', '\\sent': 'sent', '\\trash': 'trash', '\\junk': 'junk', '\\all': 'all'}
LIBELLES_DOSSIERS = {'drafts': 'Brouillons', 'sent': 'Envoyés', 'trash': 'Corbeille', 'junk': 'Indésirables', 'all': 'Tous les messages'}
ADRESSE = re.compile(r'^[^@\s,;<>"]{1,64}@[A-Za-z0-9.-]{1,190}\.[A-Za-z]{2,24}$')
CLIENT_ID = re.compile(r'^[0-9]{4,30}-[a-z0-9]{8,64}\.apps\.googleusercontent\.com$')
CLE = re.compile(r'^[A-Za-z0-9-]{8,64}$')
ROUTES_POST = ('tester', 'enregistrer', 'application')
PREFIXE = 'm56103/gmail/'
# Pages officielles citées dans l'explication (lecture seule, vérifiées le 15/11/2026)
LIENS = {'mots_de_passe': 'https://myaccount.google.com/apppasswords',
         'deux_etapes': 'https://myaccount.google.com/signinoptions/twosv',
         'aide_mots_de_passe': 'https://support.google.com/accounts/answer/185833',
         'imap_workspace': 'https://knowledge.workspace.google.com/admin/sync/turn-pop-and-imap-on-or-off-for-users',
         'console': 'https://console.cloud.google.com/',
         'gmail_api': 'https://console.cloud.google.com/apis/library/gmail.googleapis.com',
         'audience': 'https://support.google.com/cloud/answer/15549945',
         'clients': 'https://developers.google.com/workspace/guides/create-credentials',
         'portees': 'https://developers.google.com/workspace/gmail/api/auth/scopes'}

SCHEMA = '''
CREATE TABLE IF NOT EXISTS gmail_simple56103_essais(id INTEGER PRIMARY KEY AUTOINCREMENT, adresse TEXT NOT NULL, resultat TEXT NOT NULL,
  code TEXT NOT NULL DEFAULT '', motif TEXT NOT NULL DEFAULT '', dossiers TEXT NOT NULL DEFAULT '{}', secret_ref TEXT NOT NULL DEFAULT '',
  par TEXT NOT NULL DEFAULT '', quand TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS gmail_simple56103_essais_adresse ON gmail_simple56103_essais(adresse, id);
'''

MESSAGES = {
    'gmail_adresse_invalide': 'Adresse Gmail illisible : indiquez l’adresse complète du compte (terminée par gmail.com ou par le domaine Workspace du cabinet).',
    'gmail_mot_de_passe_requis': 'Collez le mot de passe d’application créé chez Google (16 caractères).',
    'gmail_mot_de_passe_application_format': 'Un mot de passe d’application Google compte 16 caractères (les espaces affichés par Google n’ont pas d’importance). '
                                             'Ne collez pas le mot de passe habituel du compte.',
    'gmail_mot_de_passe_application_requis': 'Google refuse le mot de passe habituel du compte. Activez la validation en deux étapes, créez un mot de passe '
                                             'd’application (myaccount.google.com/apppasswords), puis collez-le ici.',
    'gmail_identifiants_refuses': 'Google refuse l’adresse ou le mot de passe d’application. Vérifiez l’adresse complète et recopiez le mot de passe '
                                  'd’application (16 caractères). Après un changement du mot de passe du compte Google, les mots de passe d’application sont '
                                  'révoqués : créez-en un nouveau. Compte Google Workspace : l’administrateur a peut-être interdit les mots de passe '
                                  'd’application ou les applications sans OAuth ; utilisez alors la deuxième méthode (application interne Workspace).',
    'gmail_imap_desactive': 'Google indique que l’accès IMAP est coupé pour ce compte. Compte Google Workspace : l’administrateur doit cocher « Activer '
                            'l’accès IMAP pour tous les utilisateurs » et autoriser tous les clients de messagerie (console d’administration › Applications › '
                            'Google Workspace › Gmail › Accès des utilisateurs finaux), ou le cabinet utilise la deuxième méthode (application interne Workspace).',
    'gmail_connexion_web_requise': 'Google demande une vérification dans le navigateur avant d’accepter cette connexion : ouvrez Gmail avec ce compte, '
                                   'confirmez l’activité signalée par Google, puis relancez le test.',
    'gmail_temporairement_indisponible': 'Gmail refuse la connexion pour l’instant (trop de connexions simultanées ou service occupé) : réessayez dans quelques minutes.',
    'gmail_refus_inconnu': 'Google a refusé la connexion sans motif reconnu. Vérifiez l’adresse et le mot de passe d’application, puis réessayez.',
    'gmail_brouillons_introuvables': 'Gmail n’a annoncé aucun dossier marqué « Brouillons » (attribut \\Drafts). Dans Gmail (navigateur), Paramètres › '
                                     'Libellés : cochez « Afficher dans IMAP » pour Brouillons, puis relancez le test.',
    'gmail_brouillons_inaccessibles': 'Le dossier des brouillons annoncé par Gmail ne s’ouvre pas : relancez le test dans quelques minutes.',
    'messagerie_existante_remplacer': 'Une autre messagerie est déjà configurée pour le cabinet : cochez « Remplacer la messagerie actuelle par ce compte Gmail » '
                                      'pour confirmer.',
    'gmail_client_id_invalide': 'Identifiant client illisible : copiez-le tel que Google l’affiche (il se termine par .apps.googleusercontent.com).',
    'gmail_secret_requis': 'Collez le secret client affiché par Google à la création du client (il n’est montré qu’une fois chez Google).',
    'gmail_secret_invalide': 'Secret client illisible : collez-le sans espace ni retour à la ligne.',
    'gmail_uri_retour_invalide': 'L’URI de retour doit être en HTTPS, sans paramètre, et se terminer par /oauth/google/callback (exactement celle déclarée chez Google).',
    'gmail_audience_interne_a_confirmer': 'Cochez « J’ai choisi l’audience Interne » : cette méthode ne vaut que pour une application interne au domaine Workspace du cabinet.',
    'configuration_modifiee_recharger': 'La configuration a été modifiée par un autre écran depuis l’affichage : rechargez la page, puis recommencez.',
    'cle_idempotence_requise': 'Page incomplète : rechargez-la.', 'cle_idempotence_deja_utilisee': 'Cette demande a déjà été traitée avec d’autres valeurs : rechargez la page.',
    'role_insuffisant': 'Réglage réservé à l’administrateur du cabinet.', 'route_inconnue': 'Route inconnue.',
    'chemin_configuration_interne_absent': 'Configuration du serveur introuvable : enregistrement impossible depuis cet écran.',
    'configuration_non_inscriptible': 'Le fichier de configuration n’est pas modifiable par le service : voir le guide d’installation.'}
STATUTS = {'role_insuffisant': '403 Forbidden', 'route_inconnue': '404 Not Found', 'configuration_modifiee_recharger': '409 Conflict',
           'cle_idempotence_deja_utilisee': '409 Conflict'}


def _now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def ensure_schema(desk):
    """Création idempotente, au démarrage (``Desk``) seulement."""
    desk.db.executescript(SCHEMA)
    desk.db.commit()


def message(code):
    if code in MESSAGES:
        return MESSAGES[code]
    from .web440 import human
    texte = human(code)
    return texte if texte != code else 'Demande refusée (%s).' % code.replace('_', ' ')


# ------------------------------------------------------------------------------------------------ préréglage et contrôles
def normaliser_adresse(valeur):
    adresse = str(valeur or '').strip().lower()
    if len(adresse) > 254 or not ADRESSE.fullmatch(adresse):
        raise Stop('gmail_adresse_invalide')
    return adresse


def normaliser_mot_de_passe(valeur):
    """Google affiche le mot de passe d'application en quatre groupes de quatre : les espaces (même insécables) sont retirés.
    16 caractères alphanumériques attendus ; tout autre texte (mot de passe habituel collé par erreur) est refusé avant tout réseau."""
    brut = re.sub(r'\s+', '', str(valeur or ''), flags=re.UNICODE)
    if not brut:
        raise Stop('gmail_mot_de_passe_requis')
    if not re.fullmatch(r'[A-Za-z0-9]{16}', brut):
        raise Stop('gmail_mot_de_passe_application_format')
    return brut


def config_preset(adresse):
    """Configuration IMAP candidate (sans secret) : préréglage Gmail et identifiant = adresse complète."""
    return dict(PRESET, username=adresse, connect_timeout_seconds=DELAI_TEST)


def dossiers_speciaux(entrees):
    """{rôle: nom} à partir de ``setup.mailbox_names`` : attributs RFC 6154 seulement, jamais le nom (localisé chez Gmail).
    Un dossier « \\Noselect » est ignoré ; si deux dossiers portent le même attribut, le premier annoncé est retenu."""
    out = {}
    for nom, drapeaux in entrees:
        drapeaux = [str(d).lower() for d in drapeaux]
        if '\\noselect' in drapeaux or '\\nonexistent' in drapeaux:
            continue
        for attribut, role in ATTRIBUTS.items():
            if attribut in drapeaux and role not in out:
                out[role] = nom
    return out


def classer_refus(texte):
    """Réponse de Google à LOGIN → code clair. Le texte vient du serveur (jamais du mot de passe, qui n'est pas renvoyé par Google)."""
    t = str(texte or '').lower()
    if 'application-specific password' in t or 'app password' in t or 'application specific password' in t:
        return 'gmail_mot_de_passe_application_requis'
    if 'imap access is disabled' in t or 'not enabled for imap' in t or 'imap use' in t or 'imap is disabled' in t:
        return 'gmail_imap_desactive'
    if 'webalert' in t or 'web browser' in t or 'web login required' in t:
        return 'gmail_connexion_web_requise'
    if 'too many simultaneous' in t or '[unavailable]' in t or 'temporarily unavailable' in t or 'try again later' in t or 'overquota' in t:
        return 'gmail_temporairement_indisponible'
    if 'authenticationfailed' in t or 'invalid credentials' in t or 'authenticate failed' in t:
        return 'gmail_identifiants_refuses'
    return 'gmail_refus_inconnu'


def _motif(texte):
    """Code de réponse IMAP entre crochets seulement (« AUTHENTICATIONFAILED », « ALERT ») : jamais le texte libre du serveur."""
    m = re.search(r'\[([A-Z][A-Z-]{1,30})', str(texte or ''))
    return m[1] if m else ''


class _Boite:
    """Adaptateur minimal pour ``setup.mailbox_names`` (qui lit ``box.conn.list()``)."""
    def __init__(self, conn):
        self.conn = conn


def essayer(adresse, mot_de_passe):
    """Connexion de test, sans aucune écriture : LOGIN, LIST, EXAMINE des Brouillons. Rend {'adresse', 'dossiers'} ou lève ``Stop``
    (code clair, motif IMAP éventuel dans ``detail``). Le mot de passe ne sort pas de cette fonction."""
    from .mailbox import imap_quote, modified_utf7
    from .setup import mailbox_names
    conn = mbx.connect(config_preset(adresse))
    try:
        try:
            statut, _ = conn.login(adresse, mot_de_passe)
        except imaplib.IMAP4.error as erreur:
            code = Stop(classer_refus(erreur))
            code.detail = _motif(erreur)
            raise code from None
        except OSError:
            raise Stop('connexion_imap_echouee') from None
        if statut != 'OK':
            raise Stop('gmail_refus_inconnu')
        try:
            dossiers = dossiers_speciaux(mailbox_names(_Boite(conn)))
        except (imaplib.IMAP4.error, OSError):
            raise Stop('liste_dossiers_imap_indisponible') from None
        if 'drafts' not in dossiers:
            raise Stop('gmail_brouillons_introuvables')
        try:
            statut, _ = conn.select(imap_quote(modified_utf7(dossiers['drafts'])), readonly=True)
        except (imaplib.IMAP4.error, OSError):
            statut = 'NO'
        if statut != 'OK':
            raise Stop('gmail_brouillons_inaccessibles')
        return {'adresse': adresse, 'dossiers': dossiers}
    finally:
        try:
            conn.logout()
        except (imaplib.IMAP4.error, OSError, AttributeError):
            pass


# ------------------------------------------------------------------------------------------------ état (lecture seule)
def _mail(desk):
    return dict(desk.c.get('mail') or {})


def est_gmail(mail):
    return str(mail.get('host') or '').strip().lower() in HOTES_GMAIL


def dernier_essai(desk, adresse=None):
    try:
        if adresse:
            row = desk.db.execute('SELECT * FROM gmail_simple56103_essais WHERE adresse=? ORDER BY id DESC LIMIT 1', (adresse,)).fetchone()
        else:
            row = desk.db.execute('SELECT * FROM gmail_simple56103_essais ORDER BY id DESC LIMIT 1').fetchone()
    except Exception:   # noqa: BLE001 — table absente (installation pas encore démarrée) : aucun essai connu, jamais un faux succès
        return None
    if not row:
        return None
    out = dict(row)
    try:
        out['dossiers'] = json.loads(out['dossiers'] or '{}')
    except ValueError:
        out['dossiers'] = {}
    return out


def etat(desk):
    """État réel du chemin IMAP Gmail : messagerie du cabinet sur Gmail ou non, dernier essai, essai encore valable (même secret)."""
    mail = _mail(desk)
    gmail = est_gmail(mail)
    adresse = str(mail.get('username') or '').strip().lower() if gmail else ''
    secret = str(mail.get('password_file') or '')
    essai = dernier_essai(desk, adresse) if gmail else dernier_essai(desk)
    valable = bool(gmail and essai and essai['resultat'] == 'ok' and secret and essai['secret_ref'] == secret)
    return {'gmail': gmail, 'adresse': adresse, 'secret': bool(secret and Path(secret).is_file()), 'drafts': str(mail.get('drafts') or '') if gmail else '',
            'essai': essai, 'valable': valable, 'autre_messagerie': bool(mail.get('host')) and not gmail and 'example.com' not in str(mail.get('host'))}


def etat_application(desk):
    """Client OAuth Google du serveur (connecteur Gmail existant) : configuré ou non. Jamais le secret, ni en entier ni en partie."""
    from . import google5697 as gg
    cfg = gg.etat_configuration(desk, 'gmail')
    return {'configure': bool(cfg.get('configure')), 'uri_retour': str(cfg.get('uri_retour') or ''), 'code': str(cfg.get('code') or '')}


# ------------------------------------------------------------------------------------------------ écritures (administrateur)
def _noter(desk, adresse, resultat, code='', motif='', dossiers=None, secret_ref='', par=''):
    ident = desk.db.execute('INSERT INTO gmail_simple56103_essais(adresse,resultat,code,motif,dossiers,secret_ref,par,quand) VALUES(?,?,?,?,?,?,?,?)',
                            (adresse, resultat, code, motif, json.dumps(dossiers or {}, ensure_ascii=False), secret_ref, str(par)[:120], _now())).lastrowid
    desk.db.commit()
    desk.audit('gmail_simple56103_essai', {'adresse': adresse, 'resultat': resultat, 'code': code})
    return ident


def _essai_note(desk, adresse, mot_de_passe, par, secret_ref=''):
    """Test réel et résultat noté, succès comme échec ; l'échec est relevé ensuite (``Stop``)."""
    try:
        out = essayer(adresse, mot_de_passe)
    except Stop as erreur:
        code = str(erreur).split(':')[0]
        _noter(desk, adresse, 'echec', code, getattr(erreur, 'detail', '') or '', par=par)
        raise
    out['essai'] = _noter(desk, adresse, 'ok', dossiers=out['dossiers'], secret_ref=secret_ref, par=par)
    return out


def _secret_actuel(desk, adresse):
    """Mot de passe d'application déjà rangé dans le coffre, seulement si la messagerie du cabinet est ce même compte Gmail."""
    from .common import read_secret
    mail = _mail(desk)
    if not (est_gmail(mail) and str(mail.get('username') or '').strip().lower() == adresse and mail.get('password_file')):
        raise Stop('gmail_mot_de_passe_requis')
    return read_secret(mail['password_file']), str(mail['password_file'])


def tester(desk, owner, role, adresse, mot_de_passe=''):
    if role != 'administrateur':
        raise Stop('role_insuffisant')
    adresse = normaliser_adresse(adresse)
    if str(mot_de_passe or '').strip():
        secret, ref = normaliser_mot_de_passe(mot_de_passe), ''
    else:
        secret, ref = _secret_actuel(desk, adresse)
    out = _essai_note(desk, adresse, secret, owner, ref)
    d = out['dossiers']
    return {'ok': True, 'noreload': True, 'dossiers': d, 'message': 'Connexion à Gmail réussie. Brouillons : « %s »%s. Rien n’a été enregistré ni envoyé.' % (
        d['drafts'], (' ; Envoyés : « %s »' % d['sent']) if d.get('sent') else '')}


def _revision(data):
    try:
        return int(data.get('revision'))
    except (TypeError, ValueError):
        raise Stop('configuration_modifiee_recharger') from None


def enregistrer(desk, env, owner, role, data):
    """Test réel puis enregistrement de la messagerie du cabinet sur Gmail (``config567`` : même validation, même coffre, même révision
    que Paramètres). Remplacer une autre messagerie demande une confirmation explicite."""
    from .config567 import save
    if role != 'administrateur':
        raise Stop('role_insuffisant')
    adresse = normaliser_adresse(data.get('adresse'))
    revision = _revision(data)
    mail = _mail(desk)
    if est_gmail(mail) is False and bool(mail.get('host')) and 'example.com' not in str(mail.get('host')) and data.get('remplacer') is not True:
        raise Stop('messagerie_existante_remplacer')
    nouveau = str(data.get('mot_de_passe') or '').strip()
    if nouveau:
        secret, ref = normaliser_mot_de_passe(nouveau), ''
    else:
        secret, ref = _secret_actuel(desk, adresse)
    out = _essai_note(desk, adresse, secret, owner, ref)
    d = out['dossiers']
    valeurs = {'mail.enabled': True, 'mail.host': PRESET['host'], 'mail.port': PRESET['port'], 'mail.security': PRESET['security'],
               'mail.username': adresse, 'mail.inbox': PRESET['inbox'], 'mail.drafts': d['drafts'], 'mail.from_address': adresse}
    if d.get('sent'):
        valeurs['mail.sent'] = d['sent']
    if nouveau:
        valeurs['mail.password_file'] = secret
    resultat = save(desk, {'revision': revision, 'values': valeurs}, env)
    # le secret rangé par config567 porte un nom neuf : l'essai réussi est rattaché à ce secret (un changement ultérieur le périme)
    try:
        cfg = json.loads(Path(env['axiorhub.config_path']).read_text(encoding='utf-8'))
        ref = str(((cfg.get('mail') or {}).get('password_file')) or ref)
        desk.c.setdefault('mail', {}).update({k.split('.', 1)[1]: v for k, v in valeurs.items() if k != 'mail.password_file'})
        desk.c['mail']['password_file'] = ref
    except (OSError, ValueError, KeyError):
        pass
    desk.db.execute('UPDATE gmail_simple56103_essais SET secret_ref=? WHERE id=?', (ref, out['essai']))
    desk.db.commit()
    desk.audit('gmail_simple56103_enregistre', {'adresse': adresse, 'drafts': d['drafts'], 'revision': resultat.get('revision')})
    return {'ok': True, 'revision': resultat.get('revision'), 'dossiers': d,
            'message': 'Messagerie Gmail enregistrée : lecture par IMAP ; un brouillon n’est déposé dans « %s » que par l’action « Déposer dans '
                       'Brouillons ». Rien n’est envoyé par AxiorHub.' % d['drafts']}


def _uri_valide(uri):
    p = urlsplit(uri)
    return (p.scheme == 'https' and bool(p.hostname) and not p.username and not p.password and not p.query and not p.fragment
            and p.path.endswith('/oauth/google/callback') and len(uri) <= 400)


def enregistrer_application(desk, env, owner, role, data):
    """Application OAuth « Interne » Workspace : identifiant client, secret (coffre du serveur), URI de retour ; Gmail proposé aux avocats
    si demandé. Le secret n'est jamais renvoyé ; laissé vide, le secret déjà rangé est conservé."""
    from .config567 import save
    from . import gmail5697 as gm
    if role != 'administrateur':
        raise Stop('role_insuffisant')
    if data.get('interne') is not True:
        raise Stop('gmail_audience_interne_a_confirmer')
    client = str(data.get('client_id') or '').strip()
    if not CLIENT_ID.fullmatch(client):
        raise Stop('gmail_client_id_invalide')
    uri = str(data.get('redirect_uri') or '').strip()
    if not _uri_valide(uri):
        raise Stop('gmail_uri_retour_invalide')
    secret = str(data.get('secret') or '').strip()
    deja = etat_application(desk)['configure'] or bool(((desk.c.get('google5697') or {}).get('client_secret_file')))
    if secret:
        if len(secret) < 8 or len(secret) > 512 or re.search(r'\s', secret):
            raise Stop('gmail_secret_invalide')
    elif not deja:
        raise Stop('gmail_secret_requis')
    revision = _revision(data)
    valeurs = {'google5697.client_id': client, 'google5697.redirect_uri': uri}
    if secret:
        valeurs['google5697.client_secret_file'] = secret
    resultat = save(desk, {'revision': revision, 'values': valeurs}, env)
    try:
        cfg = json.loads(Path(env['axiorhub.config_path']).read_text(encoding='utf-8'))
        desk.c['google5697'] = dict(cfg.get('google5697') or {})
    except (OSError, ValueError, KeyError):
        pass
    if data.get('proposer') is True:
        gm.regler(desk, role, True)
    desk.audit('gmail_simple56103_application', {'client': client[:6] + '…', 'secret_remplace': bool(secret), 'revision': resultat.get('revision')})
    return {'ok': True, 'revision': resultat.get('revision'),
            'message': 'Application interne enregistrée%s. Le secret est rangé dans le coffre du serveur et ne sera plus affiché. Chaque avocat clique '
                       'ensuite « Connecter Gmail » et consent avec son propre compte du domaine.' % (' ; Gmail proposé aux avocats' if data.get('proposer') is True else '')}


# ------------------------------------------------------------------------------------------------ routes /api440/m56103/gmail/*
def allowed(role, method, api):
    route = str(api or '').split('?', 1)[0]
    route = route[len(PREFIXE):] if route.startswith(PREFIXE) else ''
    return role == 'administrateur' and method == 'POST' and route in ROUTES_POST


def _out(value, status='200 OK'):
    return {'status': status, 'kind': 'application/json; charset=utf-8', 'body': json.dumps(value, ensure_ascii=False, default=str),
            'headers': [('Cache-Control', 'private, no-store')]}


def _recu(desk, compte, route, cle, empreinte):
    from .assistant56103 import recu
    return recu(desk, compte, 'gmail/' + route, cle, empreinte)


def _garder(desk, compte, route, cle, empreinte, out):
    from .assistant56103 import garder
    garder(desk, compte, 'gmail/' + route, cle, empreinte, out)


def api(env, desk, auth, prefix, name, args, method):
    try:
        return _api(env, desk, auth, prefix, name, args, method)
    except Stop as erreur:
        code = str(erreur).split(':')[0]
        return _out({'error': code, 'message': message(code)}, STATUTS.get(code, '400 Bad Request'))


def _api(env, desk, auth, prefix, name, args, method):
    from .web567 import actor
    owner, role = actor(env)
    route = name.split('?', 1)[0][len(PREFIXE):] if name.startswith(PREFIXE) else ''
    if route not in ROUTES_POST or method != 'POST':
        raise Stop('route_inconnue')
    if role != 'administrateur':
        raise Stop('role_insuffisant')       # contrôle avant tout corps
    from .web440 import check_post
    data = check_post(env, auth)
    cle = str(data.get('cle') or '')
    if not CLE.fullmatch(cle):
        raise Stop('cle_idempotence_requise')
    # empreinte de la demande SANS le mot de passe ni le secret (jamais conservés, même hachés)
    publics = {k: v for k, v in data.items() if k not in ('mot_de_passe', 'secret', 'cle')}
    publics['_secret_fourni'] = bool(str(data.get('mot_de_passe') or data.get('secret') or '').strip())
    empreinte = hashlib.sha256(json.dumps([route, publics], sort_keys=True, default=str).encode()).hexdigest()
    if route == 'tester':   # simple vérification (aucun réglage modifié) : toujours refaite, jamais rejouée depuis un reçu
        try:
            return _out(tester(desk, owner, role, data.get('adresse'), data.get('mot_de_passe')))
        except Stop as refus:   # 5.6.103 : l'essai a répondu « non » ; c'est son résultat (200), affiché comme une erreur par la page
            code = str(refus).split(':')[0]
            return _out({'error': code, 'message': message(code), 'noreload': True, 'resultat': 'echec'})
    ancien = _recu(desk, owner, route, cle, empreinte)
    if ancien is not None:
        return _out(ancien)
    if route == 'enregistrer':
        out = enregistrer(desk, env, owner, role, data)
    else:
        out = enregistrer_application(desk, env, owner, role, data)
    _garder(desk, owner, route, cle, empreinte, out)
    return _out(out)


# ------------------------------------------------------------------------------------------------ présentation
def _quand(valeur):
    try:
        from zoneinfo import ZoneInfo
        return datetime.fromisoformat(valeur).astimezone(ZoneInfo('Europe/Paris')).strftime('%d/%m/%Y %H:%M')
    except (TypeError, ValueError):
        return ''


def _revision_config(env, desk):
    try:
        from .config567 import _path
        return int(json.loads(_path(env).read_text(encoding='utf-8')).get('config_revision567', 0))
    except Exception:   # noqa: BLE001 — révision illisible : l'enregistrement répondra 409 plutôt que d'écraser
        return int(desk.c.get('config_revision567', 0) or 0)


def _cache(nom, valeur):
    return '<input type="hidden" name="%s" value="%s">' % (e(nom, quote=True), e(str(valeur), quote=True))


def _champ(nom, libelle, valeur='', type_='text', aide='', auto='off'):
    ident = 'gs103-%s-%s' % (nom, uuid.uuid4().hex[:8])
    return ('<div class="ui89-champ"><label for="%s">%s</label><input id="%s" name="%s" type="%s" value="%s" autocomplete="%s"%s>%s</div>') % (
        ident, e(libelle), ident, e(nom, quote=True), e(type_, quote=True), e(valeur, quote=True), e(auto, quote=True),
        (' aria-describedby="%s-a"' % ident) if aide else '', ('<span class="ui89-aide" id="%s-a">%s</span>' % (ident, e(aide))) if aide else '')


def _lien(cle, libelle):
    return '<a href="%s" target="_blank" rel="noopener noreferrer">%s</a>' % (e(LIENS[cle], quote=True), e(libelle))


def etat_texte(desk):
    """Phrase d'état du chemin IMAP (pour Paramètres et l'assistant)."""
    s = etat(desk)
    essai = s['essai']
    if s['gmail'] and s['valable']:
        return 'ok', 'Messagerie du cabinet sur Gmail (%s) ; dernier test réussi le %s ; brouillons dans « %s ».' % (s['adresse'], _quand(essai['quand']), s['drafts'])
    if essai and essai['resultat'] != 'ok':
        return 'echec', 'Dernier test le %s : échec — %s' % (_quand(essai['quand']), message(essai['code']))
    if s['gmail']:
        return 'a_tester', 'Messagerie du cabinet sur Gmail (%s), pas encore testée depuis la dernière modification : relancez le test.' % s['adresse']
    return 'absent', 'Aucun compte Gmail configuré par mot de passe d’application.'


def etapes_mot_de_passe_html():
    etapes = (
        'Validation en deux étapes : dans le compte Google, ouvrez Sécurité › Validation en deux étapes (%s) et activez-la. Sans elle, Google ne propose pas '
        'de mot de passe d’application.' % LIENS['deux_etapes'],
        'Mot de passe d’application : ouvrez %s, donnez un nom (par exemple « AxiorHub Pilote »), puis « Créer ». Google affiche 16 caractères une seule fois.' % LIENS['mots_de_passe'],
        'Collez ci-dessous l’adresse Gmail complète et ces 16 caractères (les espaces n’ont pas d’importance), puis « Tester la connexion ».',
        'Si le test réussit, « Enregistrer » : AxiorHub lit la boîte par IMAP (imap.gmail.com, port 993, TLS) et ne dépose un brouillon, pièces jointes '
        'comprises, que lorsque vous cliquez « Déposer dans Brouillons ». Rien n’est envoyé.',
        'Compte Google Workspace : l’option peut être absente (comptes professionnels, protection avancée, administrateur qui impose OAuth). '
        'Utilisez alors la deuxième méthode, l’application interne Workspace.')
    return '<ol class="gs103-etapes">%s</ol><p class="ax-muted">Aide de Google : %s.</p>' % (
        ''.join('<li>%s</li>' % e(x) for x in etapes), _lien('aide_mots_de_passe', 'mots de passe d’application'))


def etapes_application_html(uri):
    etapes = (
        'Console Google Cloud (%s), avec un compte administrateur du domaine Workspace du cabinet : créez un projet dédié (par exemple « AxiorHub Gmail »).' % LIENS['console'],
        'API et services › Bibliothèque : activez « Gmail API » (%s).' % LIENS['gmail_api'],
        'Google Auth Platform › Branding (« Get Started » la première fois) : nom de l’application « AxiorHub Pilote », adresse d’assistance du cabinet ; '
        'Audience : choisissez « Interne » (seuls les comptes du domaine pourront consentir ; une application interne n’a pas besoin de la validation de Google) ; '
        'coordonnées : l’adresse de l’administrateur ; acceptez les règles des données utilisateur des API Google, puis « Créer ».',
        'Data Access › Add or remove scopes : seulement gmail.readonly et gmail.compose. Jamais gmail.modify, gmail.send ni mail.google.com.',
        'Google Auth Platform › Clients › « Create client » : type « Web application », nom « AxiorHub Pilote » ; dans « Authorized redirect URIs », '
        '« Add URI » et collez exactement : %s' % uri,
        '« Create » : Google affiche l’identifiant client (…apps.googleusercontent.com) et le secret client. Le secret n’est montré en entier qu’une fois chez '
        'Google : collez-le tout de suite ci-dessous. AxiorHub le range dans le coffre du serveur et ne l’affichera plus.',
        'Un client inutilisé pendant six mois est supprimé par Google (avis 30 jours avant) : reconnectez-vous au moins une fois dans ce délai.',
        'Ensuite chaque avocat clique « Connecter Gmail » dans la carte Gmail de cette page et consent avec son propre compte du domaine.')
    return '<ol class="gs103-etapes">%s</ol><p class="ax-muted">Documentation de Google : %s · %s · %s.</p>' % (
        ''.join('<li>%s</li>' % e(x) for x in etapes), _lien('audience', 'audience de l’application'), _lien('clients', 'création du client'),
        _lien('portees', 'portées Gmail'))


def _uri_suggeree(desk, env, prefix):
    deja = etat_application(desk)['uri_retour']
    if deja:
        return deja
    from .microsoft5636 import redirect_uri
    base = redirect_uri(env or {}, prefix).rsplit('/', 1)[0]
    return base.replace('http://', 'https://', 1) + '/api440/m5689/oauth/google/callback'


def section_html(desk, prefix, owner, role, env=None):
    """Carte « Gmail simple » de Paramètres › Connexions (et de l'assistant) : état réel, deux méthodes, formulaires (administrateur)."""
    from .ui_components5689 import formulaire
    env = env or {}
    statut, phrase = etat_texte(desk)
    s = etat(desk)
    corps = '<p class="%s" role="status">%s</p>' % ({'ok': 'ax-ok', 'echec': 'ax-alert', 'a_tester': 'ax-alert'}.get(statut, 'ax-muted'), e(phrase))
    corps += ('<p class="ax-muted">Deux méthodes, sans clé Google intégrée à AxiorHub : le mot de passe d’application (le plus simple) ou une application '
              'interne créée par le cabinet dans sa console Google Workspace.</p>')
    admin = role == 'administrateur'
    revision = _revision_config(env, desk)
    # méthode 1 : mot de passe d'application
    m1 = etapes_mot_de_passe_html()
    if admin:
        remplacer = ('<div class="ui89-champ"><label class="m5-check"><input type="checkbox" name="remplacer"> <span>Remplacer la messagerie actuelle '
                     'par ce compte Gmail</span></label></div>') if s['autre_messagerie'] else ''
        m1 += formulaire('m56103/gmail/enregistrer',
                         _cache('revision', revision) + _cache('cle', uuid.uuid4().hex)
                         + _champ('adresse', 'Adresse Gmail', s['adresse'], 'email', auto='username')
                         + _champ('mot_de_passe', 'Mot de passe d’application (16 caractères)', '', 'password',
                                  'Laisser vide pour retester le mot de passe déjà enregistré pour ce compte.' if s['gmail'] and s['secret'] else '', 'new-password')
                         + remplacer
                         + '<div class="cx96-boutons"><button class="ax-btn ghost" type="submit" data-api="m56103/gmail/tester">Tester la connexion</button> '
                           '<button class="ax-btn" type="submit">Enregistrer</button></div>', recharge=True, classe='gs103-imap')
    else:
        m1 += '<p class="ax-muted">Réglage du cabinet : demandez à l’administrateur de le faire.</p>'
    # méthode 2 : application interne Workspace (connecteur Gmail existant)
    app = etat_application(desk)
    uri = _uri_suggeree(desk, env, prefix)
    m2 = '<p role="status">%s</p>' % e('Application interne enregistrée : chaque avocat peut connecter Gmail.' if app['configure']
                                       else 'Aucune application interne enregistrée.')
    m2 += etapes_application_html(uri)
    if admin:
        m2 += formulaire('m56103/gmail/application',
                         _cache('revision', revision) + _cache('cle', uuid.uuid4().hex)
                         + '<div class="ui89-champ"><label class="m5-check"><input type="checkbox" name="interne" required> <span>J’ai choisi l’audience '
                           '« Interne » (domaine Workspace du cabinet)</span></label></div>'
                         + _champ('client_id', 'Identifiant client', ((desk.c.get('google5697') or {}).get('client_id') or '') if app['configure'] else '')
                         + _champ('secret', 'Secret client', '', 'password', 'Rangé dans le coffre du serveur, jamais réaffiché. Laisser vide pour garder le secret déjà rangé.'
                                  if app['configure'] else 'Rangé dans le coffre du serveur, jamais réaffiché.', 'new-password')
                         + _champ('redirect_uri', 'URI de retour (identique à celle déclarée chez Google)', uri, 'url')
                         + '<div class="ui89-champ"><label class="m5-check"><input type="checkbox" name="proposer" checked> <span>Proposer Gmail aux avocats '
                           'du cabinet</span></label></div>'
                         + '<div class="cx96-boutons"><button class="ax-btn" type="submit">Enregistrer l’application</button></div>', recharge=True, classe='gs103-app')
    return ('<section class="ax-card" id="gmail56103" aria-labelledby="gmail56103-t"><h2 id="gmail56103-t">Compte Google (Gmail)</h2>%s'
            '<details class="gm97-guide" open><summary>Méthode 1 (conseillée) : mot de passe d’application</summary>%s</details>'
            '<details class="gm97-guide"%s><summary>Méthode 2 : application interne Google Workspace</summary>%s</details></section>') % (
        corps, m1, ' open' if app['configure'] else '', m2)
