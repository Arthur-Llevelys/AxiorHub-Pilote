"""5.6.106 (lot P) : réglage prêt à l'emploi pour une adresse @avocat.fr (messagerie du CNB), IMAP et SMTP.

Demande du cabinet (16/11/2026) : l'avocat n'a qu'à **confirmer ou corriger son adresse** (« prénom.nom » suivie du domaine avocat.fr) et **saisir son mot de
passe applicatif**. Paramètres officiels, tels que relevés dans les pages d'assistance du CNB (« paramètres imaps/smtps », « ThunderBird »,
« mot de passe applicatif ») le 16/11/2026, sans rien y ajouter :

- IMAP : ``imaps.avocat.fr``, port 993, SSL/TLS (TLS implicite), mot de passe normal ;
- SMTP : ``smtps.avocat.fr``, port 465, SSL/TLS (TLS implicite), mot de passe normal ;
- identifiant IMAP et SMTP : l'adresse complète en @avocat.fr ;
- mot de passe : le **mot de passe applicatif** créé sur https://auth.avocat.fr/deviceaccounts (Clé Avocat ou e-dentitas, « Ajouter un
  compte », nom d'appareil, « Créer le compte » ; affiché une seule fois), jamais le mot de passe habituel.

Agendas : l'aide du CNB cite CalDAV (agendas Twake) sans en publier l'adresse ; aucun réglage d'agenda n'est donc proposé ici.

Même mécanique que le réglage Gmail simplifié de la 5.6.103 (``gmail_simple56103``), dont ce module est un préréglage de plus :
mêmes reçus d'idempotence (``assistant56103.recu``/``garder``), même enregistrement (``config567.save`` : validation, coffre, révision 409),
même détection des dossiers spéciaux par attribut RFC 6154 (complétée ici de ``\\Archive`` et, à défaut d'attribut, des noms usuels),
mêmes champs cachés et même présentation. Ce qui diffère : deux serveurs (IMAP puis SMTP), aucun format imposé au mot de passe (le CNB n'en
publie pas), un domaine attendu (@avocat.fr : simple avertissement s'il diffère) et une adresse proposée d'après le nom de la personne.

Proposition d'adresse (``proposer_adresse``) : **une proposition, toujours modifiable**. Minuscules, accents et cédilles retirés, ligatures
dépliées (œ → oe), apostrophes supprimées (« d'Orlandec » → « dorlandec »), espaces remplacés par des tirets (« de La Fontaine » →
« de-la-fontaine ») et traits d'union gardés : c'est la règle retenue pour les noms composés, faute d'une règle publiée par le CNB.
Le nom de famille est le mot écrit en capitales (« Jeanne DURAND », « DURAND Jeanne »), avec les particules qui le précèdent ; sans
capitales, le premier mot est le prénom.

Test (« Tester ») : connexion IMAP (LOGIN, LIST) puis SMTP (EHLO, AUTH) ; **aucun message n'est jamais envoyé** (ni MAIL, ni RCPT, ni
DATA). Erreurs traduites : mot de passe refusé (« Vérifiez le mot de passe applicatif »), serveur injoignable, certificat invalide.
Le résultat (succès comme échec) est noté dans ``avocatfr56106_essais``, sans le mot de passe.

Mot de passe : jamais écrit dans la configuration, un journal, une réponse ni la base ; à l'enregistrement, ``config567`` le range dans
le coffre (``vault567``), une fois pour IMAP et une fois pour SMTP, comme tout autre secret de Paramètres.

Écritures : administrateur seulement (la messagerie est un réglage du cabinet), CSRF, clé d'idempotence, révision de la configuration
(409). Schéma créé au démarrage (``Desk`` → ``assistant56103.ensure_schema``), jamais pendant une lecture.
"""
from datetime import datetime, timezone
from html import escape as e
import hashlib
import imaplib
import json
from pathlib import Path
import re
import smtplib
import socket
import ssl
import unicodedata
import uuid

from .common import Stop
from . import gmail_simple56103 as gs   # mécanique commune (dossiers RFC 6154, révision, champs cachés, dates)
from . import mailbox as mbx            # référence au module : la connexion IMAP est remplaçable par les essais (faux serveur)

DOMAINE = 'avocat.fr'
FORME = 'prénom.nom' + '@' + DOMAINE   # forme attendue, assemblée à l'exécution (aucune adresse complète écrite dans le code)
IMAP = {'host': 'imaps.avocat.fr', 'port': 993, 'security': 'ssl'}
SMTP = {'host': 'smtps.avocat.fr', 'port': 465, 'security': 'ssl'}
INBOX = 'INBOX'
DELAI_TEST = 20                       # secondes : connexions de test bornées
LIEN_MOT_DE_PASSE = 'https://auth.avocat.fr/deviceaccounts'
PREFIXE = 'm56106/avocatfr/'
ROUTES_POST = ('tester', 'enregistrer')
CLE = re.compile(r'^[A-Za-z0-9-]{8,64}$')
HOTE = re.compile(r'^[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$')
LIBELLES_SECURITE = {'ssl': 'SSL/TLS (TLS implicite)', 'starttls': 'STARTTLS'}
PARTICULES = ('de', 'du', 'des', 'd', 'la', 'le', 'les', 'van', 'von', 'der', 'den', 'da', 'di', 'del', 'della', 'dos', 'das', 'ben', 'ibn', 'el', 'al', 'mac', 'mc', 'saint', 'sainte')
APOSTROPHES = "'’ʼ‘`´"
LIGATURES = {'œ': 'oe', 'Œ': 'OE', 'æ': 'ae', 'Æ': 'AE', 'ß': 'ss', 'ø': 'o', 'Ø': 'O', 'ł': 'l', 'Ł': 'L', 'đ': 'd', 'Đ': 'D'}
# Dossiers spéciaux : attribut RFC 6154 d'abord (gmail_simple56103.ATTRIBUTS + \Archive), sinon nom usuel (dernier segment, sans casse)
ATTRIBUTS = {**gs.ATTRIBUTS, '\\archive': 'archive'}
NOMS_USUELS = {'drafts': ('drafts', 'draft', 'brouillons', 'brouillon'),
               'sent': ('sent', 'sent items', 'sent messages', 'sent mail', 'envoyés', 'éléments envoyés', 'messages envoyés', 'envoyes'),
               'junk': ('junk', 'spam', 'junk e-mail', 'junk email', 'indésirables', 'courrier indésirable', 'pourriel', 'indesirables'),
               'trash': ('trash', 'corbeille', 'deleted items', 'deleted messages', 'éléments supprimés', 'elements supprimes'),
               'archive': ('archive', 'archives')}
LIBELLES_DOSSIERS = {'drafts': 'Brouillons', 'sent': 'Envoyés', 'junk': 'Indésirables', 'trash': 'Corbeille', 'archive': 'Archives'}

SCHEMA = '''
CREATE TABLE IF NOT EXISTS avocatfr56106_essais(id INTEGER PRIMARY KEY AUTOINCREMENT, adresse TEXT NOT NULL, resultat TEXT NOT NULL,
  etape TEXT NOT NULL DEFAULT '', code TEXT NOT NULL DEFAULT '', motif TEXT NOT NULL DEFAULT '', dossiers TEXT NOT NULL DEFAULT '{}',
  reglages TEXT NOT NULL DEFAULT '{}', secret_ref TEXT NOT NULL DEFAULT '', par TEXT NOT NULL DEFAULT '', quand TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS avocatfr56106_essais_adresse ON avocatfr56106_essais(adresse, id);
'''

MESSAGES = {
    'avocatfr_adresse_invalide': 'Adresse illisible : indiquez l’adresse complète, par exemple %s.' % FORME,
    'avocatfr_mot_de_passe_requis': 'Saisissez le mot de passe applicatif créé sur auth.avocat.fr/deviceaccounts (pas le mot de passe habituel).',
    'avocatfr_mot_de_passe_illisible': 'Mot de passe applicatif illisible : collez-le sans retour à la ligne.',
    'avocatfr_mot_de_passe_refuse': 'Le serveur avocat.fr refuse l’adresse ou le mot de passe. Vérifiez le mot de passe applicatif : ce n’est pas le mot de '
                                    'passe habituel de la messagerie. S’il est perdu, supprimez-le sur auth.avocat.fr/deviceaccounts et recréez-en un.',
    'avocatfr_serveur_injoignable': 'Serveur de messagerie avocat.fr injoignable : vérifiez la connexion Internet du serveur AxiorHub, puis réessayez.',
    'avocatfr_certificat_invalide': 'Le certificat présenté par le serveur n’est pas valide pour cette adresse : connexion interrompue, rien n’a été transmis. '
                                    'Vérifiez l’adresse du serveur (réglages avancés) ou réessayez plus tard.',
    'avocatfr_tls_echoue': 'La connexion chiffrée (SSL/TLS) a échoué : vérifiez le port et la sécurité dans les réglages avancés.',
    'avocatfr_serveur_indisponible': 'Le serveur avocat.fr refuse la connexion pour l’instant : réessayez dans quelques minutes.',
    'avocatfr_smtp_auth_indisponible': 'Le serveur d’envoi n’accepte pas d’authentification sur ce port : vérifiez les réglages avancés (smtps.avocat.fr, '
                                       'port 465, SSL/TLS).',
    'avocatfr_dossiers_illisibles': 'Connexion acceptée, mais la liste des dossiers n’a pas pu être lue : réessayez dans quelques minutes.',
    'avocatfr_brouillons_introuvables': 'Connexion acceptée, mais aucun dossier « Brouillons » n’a été trouvé dans la messagerie : créez-le dans le webmail '
                                        'avocat.fr, puis relancez le test.',
    'avocatfr_reglage_avance_invalide': 'Réglages avancés illisibles : serveur sans espace ni barre oblique, port de 1 à 65535, sécurité SSL/TLS ou STARTTLS.',
    'messagerie_existante_remplacer': 'Une autre messagerie est déjà configurée pour le cabinet : cochez « Remplacer la messagerie actuelle par cette adresse '
                                      'avocat.fr » pour confirmer.',
    'configuration_modifiee_recharger': 'La configuration a été modifiée par un autre écran depuis l’affichage : rechargez la page, puis recommencez.',
    'cle_idempotence_requise': 'Page incomplète : rechargez-la.', 'cle_idempotence_deja_utilisee': 'Cette demande a déjà été traitée avec d’autres valeurs : rechargez la page.',
    'role_insuffisant': 'Réglage réservé à l’administrateur du cabinet.', 'route_inconnue': 'Route inconnue.',
    'configuration_non_inscriptible': 'Le fichier de configuration n’est pas modifiable par le service : voir le guide d’installation.'}
STATUTS = {'role_insuffisant': '403 Forbidden', 'route_inconnue': '404 Not Found', 'configuration_modifiee_recharger': '409 Conflict',
           'cle_idempotence_deja_utilisee': '409 Conflict'}
ETAPES = {'imap': 'réception (IMAP)', 'smtp': 'envoi (SMTP)'}
# Causes de ``mailbox.connect`` → message de ce réglage
CAUSES_IMAP = {'imap_serveur_introuvable': 'avocatfr_serveur_injoignable', 'imap_port_ferme': 'avocatfr_serveur_injoignable',
               'imap_delai_depasse': 'avocatfr_serveur_injoignable', 'connexion_imap_echouee': 'avocatfr_serveur_injoignable',
               'imap_certificat_refuse': 'avocatfr_certificat_invalide', 'imap_tls_echoue': 'avocatfr_tls_echoue',
               'imap_port_sans_tls_direct': 'avocatfr_tls_echoue', 'imap_starttls_indisponible': 'avocatfr_tls_echoue'}


def _now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def ensure_schema(desk):
    """Création idempotente, au démarrage (``Desk``, par ``assistant56103.ensure_schema``) seulement."""
    desk.db.executescript(SCHEMA)
    desk.db.commit()


def message(code):
    return MESSAGES.get(code) or gs.message(code)


def _stop(code, etape='', motif=''):
    erreur = Stop(code)
    erreur.etape, erreur.detail = etape, motif
    return erreur


# ------------------------------------------------------------------------------------------------ proposition d'adresse
def _ascii(texte):
    texte = ''.join(LIGATURES.get(c, c) for c in str(texte or ''))
    return ''.join(c for c in unicodedata.normalize('NFD', texte) if not unicodedata.combining(c))


def _segment(mots):
    """Mots → segment d'adresse : minuscules, apostrophes supprimées, espaces en tirets, rien d'autre que a-z, 0-9 et « - »."""
    texte = '-'.join(mots)
    for a in APOSTROPHES:
        texte = texte.replace(a, '')
    texte = re.sub(r'[^a-z0-9-]+', '-', _ascii(texte).lower())
    return re.sub(r'-{2,}', '-', texte).strip('-')


def _capitales(mot):
    lettres = [c for c in _ascii(mot) if c.isalpha()]
    return len(lettres) >= 2 and all(c.isupper() for c in lettres)


def _est_particule(mot):
    brut = _ascii(mot).lower()
    for a in APOSTROPHES:
        brut = brut.replace(a, '')
    return brut in PARTICULES


def separer_nom(nom_complet):
    """(prénom, nom) d'après le nom affiché. Nom de famille : les mots en capitales (et les particules qui les précèdent) ;
    sinon tout ce qui suit le premier mot. Un mot « d'ORLANDEC » est découpé à l'apostrophe pour reconnaître la particule."""
    mots = [m for m in re.split(r'\s+', str(nom_complet or '').strip()) if m][:12]
    if len(mots) < 2:
        return (mots[0] if mots else ''), ''
    caps = [i for i, m in enumerate(mots) if _capitales(re.split('[' + APOSTROPHES + ']', m)[-1])]
    if caps and len(caps) < len(mots):
        debut, fin = caps[0], caps[-1]
        while debut > 0 and _est_particule(mots[debut - 1]):
            debut -= 1
        famille = mots[debut:fin + 1]
        prenom = mots[:debut] + mots[fin + 1:]
        return ' '.join(prenom), ' '.join(famille)
    return mots[0], ' '.join(mots[1:])


def proposer_adresse(nom_complet):
    """Proposition « prénom.nom » suivie de « @ » et du domaine ``DOMAINE`` (toujours modifiable). Chaîne vide si le nom ne permet pas de la former.
    Un identifiant de la forme « prénom.nom@… » est repris tel quel (partie locale normalisée)."""
    brut = str(nom_complet or '').strip()[:200]
    if '@' in brut:
        local = brut.split('@', 1)[0]
        morceaux = [_segment([m]) for m in local.split('.')]
        return ('%s@%s' % ('.'.join(morceaux), DOMAINE)) if len(morceaux) >= 2 and all(morceaux) else ''
    prenom, nom = separer_nom(brut)
    p, n = _segment(prenom.split()), _segment(nom.split())
    return '%s.%s@%s' % (p, n, DOMAINE) if p and n else ''


def nom_personne(desk, env, owner):
    """Nom affiché de la personne connectée : compte autonome, en-tête Nextcloud, annuaire des membres, sinon l'identifiant."""
    env = env or {}
    user = env.get('axiorhub.user') or {}
    for valeur in ((user.get('name') if isinstance(user, dict) else ''), env.get('HTTP_X_AXIORHUB_NAME')):
        if str(valeur or '').strip():
            return str(valeur).strip()[:120]
    try:
        row = desk.db.execute('SELECT name FROM membres5650 WHERE owner=?', (owner,)).fetchone()
        if row and str(row[0] or '').strip():
            return str(row[0]).strip()[:120]
    except Exception:   # noqa: BLE001 — annuaire pas encore créé : aucune proposition par ce moyen
        pass
    return str(owner or '') if '@' in str(owner or '') else ''


def domaine_attendu(adresse):
    return str(adresse or '').strip().lower().endswith('@' + DOMAINE)


def avertissement_domaine(adresse):
    if domaine_attendu(adresse):
        return ''
    return 'Attention : cette adresse ne se termine pas par @avocat.fr. Les serveurs proposés sont ceux du CNB ; pour une autre messagerie, utilisez les réglages avancés ou la rubrique Messagerie.'


# ------------------------------------------------------------------------------------------------ contrôles
def normaliser_adresse(valeur):
    adresse = str(valeur or '').strip().lower()
    if len(adresse) > 254 or not gs.ADRESSE.fullmatch(adresse):
        raise Stop('avocatfr_adresse_invalide')
    return adresse


def normaliser_mot_de_passe(valeur):
    """Aucun format publié par le CNB : seuls les blancs aux extrémités sont retirés ; retour à la ligne ou caractère de contrôle refusés."""
    brut = str(valeur or '').strip()
    if not brut:
        raise Stop('avocatfr_mot_de_passe_requis')
    if len(brut) > 512 or any(ord(c) < 32 for c in brut):
        raise Stop('avocatfr_mot_de_passe_illisible')
    return brut


def _port(valeur):
    try:
        if isinstance(valeur, bool):
            raise ValueError()
        n = int(str(valeur).strip())
    except (TypeError, ValueError):
        raise Stop('avocatfr_reglage_avance_invalide') from None
    if not 1 <= n <= 65535:
        raise Stop('avocatfr_reglage_avance_invalide')
    return n


def reglages(data):
    """Serveurs, ports et sécurité : le préréglage du CNB, figé, sauf si « Utiliser ces réglages avancés » est coché."""
    data = data or {}
    if data.get('avance') is not True:
        return {'imap': dict(IMAP), 'smtp': dict(SMTP), 'avance': False}
    out = {'avance': True}
    for nom, defaut in (('imap', IMAP), ('smtp', SMTP)):
        hote = str(data.get(nom + '_host') or defaut['host']).strip().lower()
        securite = str(data.get(nom + '_security') or defaut['security']).strip().lower()
        if not HOTE.fullmatch(hote) or securite not in LIBELLES_SECURITE:
            raise Stop('avocatfr_reglage_avance_invalide')
        out[nom] = {'host': hote, 'port': _port(data.get(nom + '_port') or defaut['port']), 'security': securite}
    return out


def dossiers_speciaux(entrees):
    """{rôle: nom} : attribut RFC 6154 d'abord (``\\Drafts``, ``\\Sent``, ``\\Junk``, ``\\Trash``, ``\\Archive``), sinon nom usuel
    (dernier segment du chemin, sans casse ni accent). Un dossier ``\\Noselect`` est ignoré ; le premier annoncé l'emporte."""
    entrees = [(nom, [str(d).lower() for d in drapeaux]) for nom, drapeaux in entrees]
    out = {}
    for nom, drapeaux in entrees:
        if '\\noselect' in drapeaux or '\\nonexistent' in drapeaux:
            continue
        for attribut, role in ATTRIBUTS.items():
            if attribut in drapeaux and role in LIBELLES_DOSSIERS and role not in out:
                out[role] = nom
    for role, noms in NOMS_USUELS.items():
        if role in out:
            continue
        connus = {_ascii(n).lower() for n in noms}
        for nom, drapeaux in entrees:
            if '\\noselect' in drapeaux or '\\nonexistent' in drapeaux or nom.upper() == INBOX:
                continue
            dernier = re.split(r'[./]', nom)[-1]
            if _ascii(dernier).strip().lower() in connus and nom not in out.values():
                out[role] = nom
                break
    return out


# ------------------------------------------------------------------------------------------------ test (aucune écriture, aucun envoi)
def _motif(texte):
    m = re.search(r'\[([A-Z][A-Z-]{1,30})', str(texte or ''))
    return m[1] if m else ''


def essayer_imap(reglage, adresse, mot_de_passe):
    """LOGIN puis LIST ; rend les dossiers spéciaux. Le mot de passe ne sort pas de cette fonction."""
    from .setup import mailbox_names
    try:
        conn = mbx.connect(dict(reglage, connect_timeout_seconds=DELAI_TEST))
    except Stop as erreur:
        raise _stop(CAUSES_IMAP.get(str(erreur).split(':')[0], 'avocatfr_serveur_injoignable'), 'imap') from None
    try:
        try:
            statut, _ = conn.login(adresse, mot_de_passe)
        except imaplib.IMAP4.error as erreur:
            motif = _motif(erreur)
            code = 'avocatfr_serveur_indisponible' if motif in ('UNAVAILABLE', 'INUSE', 'LIMIT', 'OVERQUOTA') else 'avocatfr_mot_de_passe_refuse'
            raise _stop(code, 'imap', motif) from None
        except OSError:
            raise _stop('avocatfr_serveur_injoignable', 'imap') from None
        if statut != 'OK':
            raise _stop('avocatfr_mot_de_passe_refuse', 'imap')
        try:
            dossiers = dossiers_speciaux(mailbox_names(gs._Boite(conn)))
        except (Stop, imaplib.IMAP4.error, OSError):
            raise _stop('avocatfr_dossiers_illisibles', 'imap') from None
        if 'drafts' not in dossiers:
            raise _stop('avocatfr_brouillons_introuvables', 'imap')
        return dossiers
    finally:
        try:
            conn.logout()
        except (imaplib.IMAP4.error, OSError, AttributeError):
            pass


def essayer_smtp(reglage, adresse, mot_de_passe):
    """EHLO puis AUTH, puis QUIT : jamais MAIL, RCPT ni DATA (aucun message envoyé)."""
    from .common import release_before_network
    release_before_network('smtp')
    contexte = ssl.create_default_context()
    client = None
    try:
        if reglage['security'] == 'ssl':
            client = smtplib.SMTP_SSL(reglage['host'], int(reglage['port']), timeout=DELAI_TEST, context=contexte)
            client.ehlo()
        else:
            client = smtplib.SMTP(reglage['host'], int(reglage['port']), timeout=DELAI_TEST)
            client.ehlo()
            if not client.has_extn('starttls'):
                raise _stop('avocatfr_tls_echoue', 'smtp')
            client.starttls(context=contexte)
            client.ehlo()
        if not client.has_extn('auth'):
            raise _stop('avocatfr_smtp_auth_indisponible', 'smtp')
        client.login(adresse, mot_de_passe)
        return True
    except Stop:
        raise
    except smtplib.SMTPAuthenticationError:
        raise _stop('avocatfr_mot_de_passe_refuse', 'smtp') from None
    except smtplib.SMTPNotSupportedError:
        raise _stop('avocatfr_smtp_auth_indisponible', 'smtp') from None
    except ssl.SSLCertVerificationError:
        raise _stop('avocatfr_certificat_invalide', 'smtp') from None
    except ssl.SSLError:
        raise _stop('avocatfr_tls_echoue', 'smtp') from None
    except smtplib.SMTPResponseException as erreur:
        raise _stop('avocatfr_serveur_indisponible' if 400 <= int(erreur.smtp_code or 0) < 500 else 'avocatfr_mot_de_passe_refuse', 'smtp') from None
    except (smtplib.SMTPException, socket.gaierror, ConnectionError, TimeoutError, socket.timeout, OSError):
        raise _stop('avocatfr_serveur_injoignable', 'smtp') from None
    finally:
        if client is not None:
            try:
                client.quit()
            except (smtplib.SMTPException, OSError):
                try:
                    client.close()
                except OSError:
                    pass


def essayer(reglage, adresse, mot_de_passe, mot_de_passe_smtp=None):
    """IMAP puis SMTP ; rend {'adresse', 'dossiers'} ou lève ``Stop`` (``etape`` : imap ou smtp ; ``detail`` : code IMAP éventuel)."""
    dossiers = essayer_imap(reglage['imap'], adresse, mot_de_passe)
    essayer_smtp(reglage['smtp'], adresse, mot_de_passe if mot_de_passe_smtp is None else mot_de_passe_smtp)
    return {'adresse': adresse, 'dossiers': dossiers}


# ------------------------------------------------------------------------------------------------ état (lecture seule)
def _mail(desk):
    return dict(desk.c.get('mail') or {})


def _smtp(desk):
    mail = _mail(desk)
    return dict(mail.get('smtp') or desk.c.get('smtp') or {})


def est_avocatfr(mail):
    return str(mail.get('host') or '').strip().lower() == IMAP['host'] or domaine_attendu(mail.get('username'))


def dernier_essai(desk, adresse=None):
    try:
        if adresse:
            row = desk.db.execute('SELECT * FROM avocatfr56106_essais WHERE adresse=? ORDER BY id DESC LIMIT 1', (adresse,)).fetchone()
        else:
            row = desk.db.execute('SELECT * FROM avocatfr56106_essais ORDER BY id DESC LIMIT 1').fetchone()
    except Exception:   # noqa: BLE001 — table absente (installation pas encore démarrée) : aucun essai connu, jamais un faux succès
        return None
    if not row:
        return None
    out = dict(row)
    for cle in ('dossiers', 'reglages'):
        try:
            out[cle] = json.loads(out[cle] or '{}')
        except ValueError:
            out[cle] = {}
    return out


def etat(desk):
    """État réel : messagerie du cabinet sur avocat.fr ou non, dernier essai, essai encore valable (même secret en service)."""
    mail = _mail(desk)
    avocat = est_avocatfr(mail)
    adresse = str(mail.get('username') or '').strip().lower() if avocat else ''
    secret = str(mail.get('password_file') or '')
    essai = dernier_essai(desk, adresse) if avocat else dernier_essai(desk)
    valable = bool(avocat and essai and essai['resultat'] == 'ok' and secret and essai['secret_ref'] == secret)
    smtp = _smtp(desk)
    return {'avocat': avocat, 'adresse': adresse, 'secret': bool(secret and Path(secret).is_file()), 'essai': essai, 'valable': valable,
            'drafts': str(mail.get('drafts') or '') if avocat else '', 'smtp': bool(smtp.get('host')) and str(smtp.get('username') or '').lower() == adresse,
            'imap_actuel': {'host': str(mail.get('host') or ''), 'port': mail.get('port') or '', 'security': str(mail.get('security') or '')},
            'smtp_actuel': {'host': str(smtp.get('host') or ''), 'port': smtp.get('port') or '', 'security': str(smtp.get('security') or '')},
            'autre_messagerie': bool(mail.get('host')) and not avocat and 'example.com' not in str(mail.get('host'))}


def etat_texte(desk):
    s = etat(desk)
    essai = s['essai']
    if s['avocat'] and s['valable']:
        return 'ok', 'Messagerie du cabinet sur avocat.fr (%s) ; dernier test réussi le %s ; réception et envoi vérifiés.' % (s['adresse'], gs._quand(essai['quand']))
    if essai and essai['resultat'] != 'ok':
        return 'echec', 'Dernier test le %s : échec (%s) — %s' % (gs._quand(essai['quand']), ETAPES.get(essai.get('etape'), 'connexion'), message(essai['code']))
    if s['avocat']:
        return 'a_tester', 'Messagerie du cabinet sur avocat.fr (%s), pas encore testée depuis la dernière modification : relancez le test.' % s['adresse']
    return 'absent', 'Aucune adresse avocat.fr configurée.'


# ------------------------------------------------------------------------------------------------ écritures (administrateur)
def _noter(desk, adresse, resultat, reglage, etape='', code='', motif='', dossiers=None, secret_ref='', par=''):
    publics = {k: reglage[k] for k in ('imap', 'smtp', 'avance')}
    ident = desk.db.execute('INSERT INTO avocatfr56106_essais(adresse,resultat,etape,code,motif,dossiers,reglages,secret_ref,par,quand) VALUES(?,?,?,?,?,?,?,?,?,?)',
                            (adresse, resultat, etape, code, motif, json.dumps(dossiers or {}, ensure_ascii=False), json.dumps(publics), secret_ref,
                             str(par)[:120], _now())).lastrowid
    desk.db.commit()
    desk.audit('avocatfr56106_essai', {'adresse': adresse, 'resultat': resultat, 'etape': etape, 'code': code})
    return ident


def _essai_note(desk, reglage, adresse, mot_de_passe, par, secret_ref='', mot_de_passe_smtp=None):
    """Test réel et résultat noté, succès comme échec (sans le mot de passe) ; l'échec est relevé ensuite (``Stop``)."""
    try:
        out = essayer(reglage, adresse, mot_de_passe, mot_de_passe_smtp)
    except Stop as erreur:
        _noter(desk, adresse, 'echec', reglage, getattr(erreur, 'etape', ''), str(erreur).split(':')[0], getattr(erreur, 'detail', '') or '', par=par)
        raise
    out['essai'] = _noter(desk, adresse, 'ok', reglage, dossiers=out['dossiers'], secret_ref=secret_ref, par=par)
    return out


def _secrets_actuels(desk, adresse):
    """Mots de passe déjà rangés dans le coffre, seulement si la messagerie du cabinet (IMAP et SMTP) est ce même compte."""
    from .common import read_secret
    mail, smtp = _mail(desk), _smtp(desk)
    if not (str(mail.get('username') or '').strip().lower() == adresse and mail.get('password_file')
            and str(smtp.get('username') or '').strip().lower() == adresse and smtp.get('password_file')):
        raise Stop('avocatfr_mot_de_passe_requis')
    return read_secret(mail['password_file']), read_secret(smtp['password_file']), str(mail['password_file'])


def _phrase_dossiers(d):
    return ' ; '.join('%s : « %s »' % (LIBELLES_DOSSIERS[r], d[r]) for r in LIBELLES_DOSSIERS if d.get(r))


def tester(desk, owner, role, data):
    if role != 'administrateur':
        raise Stop('role_insuffisant')
    adresse = normaliser_adresse(data.get('adresse'))
    reglage = reglages(data)
    if str(data.get('mot_de_passe') or '').strip():
        secret = normaliser_mot_de_passe(data.get('mot_de_passe'))
        out = _essai_note(desk, reglage, adresse, secret, owner)
    else:
        imap_mdp, smtp_mdp, ref = _secrets_actuels(desk, adresse)
        out = _essai_note(desk, reglage, adresse, imap_mdp, owner, ref, smtp_mdp)
    avert = avertissement_domaine(adresse)
    return {'ok': True, 'noreload': True, 'dossiers': out['dossiers'], 'avertissement': avert,
            'message': ('Connexion réussie : réception (IMAP) et envoi (SMTP) acceptés. %s. Aucun message n’a été envoyé et rien n’a été enregistré.%s' % (
                _phrase_dossiers(out['dossiers']), (' ' + avert) if avert else ''))}


def enregistrer(desk, env, owner, role, data):
    """Test réel (IMAP puis SMTP) puis enregistrement par ``config567`` (même validation, même coffre, même révision que Paramètres)."""
    from .config567 import save
    if role != 'administrateur':
        raise Stop('role_insuffisant')
    adresse = normaliser_adresse(data.get('adresse'))
    reglage = reglages(data)
    revision = gs._revision(data)
    s = etat(desk)
    if s['autre_messagerie'] and data.get('remplacer') is not True:
        raise Stop('messagerie_existante_remplacer')
    nouveau = str(data.get('mot_de_passe') or '').strip()
    if nouveau:
        secret = normaliser_mot_de_passe(nouveau)
        out = _essai_note(desk, reglage, adresse, secret, owner)
    else:
        imap_mdp, smtp_mdp, ref = _secrets_actuels(desk, adresse)
        out = _essai_note(desk, reglage, adresse, imap_mdp, owner, ref, smtp_mdp)
    d = out['dossiers']
    i, m = reglage['imap'], reglage['smtp']
    valeurs = {'mail.enabled': True, 'mail.host': i['host'], 'mail.port': i['port'], 'mail.security': i['security'], 'mail.username': adresse,
               'mail.inbox': INBOX, 'mail.drafts': d['drafts'], 'mail.from_address': adresse,
               'smtp.enabled': True, 'smtp.host': m['host'], 'smtp.port': m['port'], 'smtp.security': m['security'], 'smtp.username': adresse,
               'smtp.from_address': adresse}
    if d.get('sent'):
        valeurs['mail.sent'] = d['sent']
    if nouveau:
        valeurs['mail.password_file'] = secret          # rangé dans le coffre par config567 (nom neuf), jamais écrit en clair
        valeurs['smtp.password_file'] = secret
    resultat = save(desk, {'revision': revision, 'values': valeurs}, env)
    ref = ''
    try:   # configuration en mémoire alignée sur le fichier enregistré ; l'essai réussi est rattaché au secret en service
        cfg = json.loads(Path(env['axiorhub.config_path']).read_text(encoding='utf-8'))
        for section in ('mail', 'smtp'):
            gardes = {k.split('.', 1)[1]: v for k, v in valeurs.items() if k.startswith(section + '.') and not k.endswith('password_file')}
            gardes['password_file'] = str(((cfg.get(section) or {}).get('password_file')) or (desk.c.get(section) or {}).get('password_file') or '')
            desk.c.setdefault(section, {}).update(gardes)
        ref = str(desk.c['mail'].get('password_file') or '')
    except (OSError, ValueError, KeyError):
        pass
    desk.db.execute('UPDATE avocatfr56106_essais SET secret_ref=? WHERE id=?', (ref, out['essai']))
    desk.db.commit()
    desk.audit('avocatfr56106_enregistre', {'adresse': adresse, 'drafts': d['drafts'], 'avance': reglage['avance'], 'revision': resultat.get('revision')})
    avert = avertissement_domaine(adresse)
    return {'ok': True, 'revision': resultat.get('revision'), 'dossiers': d, 'avertissement': avert,
            'message': ('Adresse %s enregistrée : réception par IMAP, envoi par SMTP (toujours sur votre confirmation explicite). %s. Le mot de passe '
                        'applicatif est rangé dans le coffre.%s' % (adresse, _phrase_dossiers(d), (' ' + avert) if avert else ''))}


# ------------------------------------------------------------------------------------------------ routes /api440/m56106/avocatfr/*
def allowed(role, method, api):
    route = str(api or '').split('?', 1)[0]
    route = route[len(PREFIXE):] if route.startswith(PREFIXE) else ''
    return role == 'administrateur' and method == 'POST' and route in ROUTES_POST


def _out(value, status='200 OK'):
    return {'status': status, 'kind': 'application/json; charset=utf-8', 'body': json.dumps(value, ensure_ascii=False, default=str),
            'headers': [('Cache-Control', 'private, no-store')]}


def api(env, desk, auth, prefix, name, args, method):
    try:
        return _api(env, desk, auth, prefix, name, args, method)
    except Stop as erreur:
        code = str(erreur).split(':')[0]
        corps = {'error': code, 'message': message(code)}
        if getattr(erreur, 'etape', ''):
            corps['etape'] = erreur.etape
        return _out(corps, STATUTS.get(code, '400 Bad Request'))


def _api(env, desk, auth, prefix, name, args, method):
    from .web567 import actor
    from .assistant56103 import recu, garder
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
    # empreinte de la demande SANS le mot de passe (jamais conservé, même haché)
    publics = {k: v for k, v in data.items() if k not in ('mot_de_passe', 'cle')}
    publics['_secret_fourni'] = bool(str(data.get('mot_de_passe') or '').strip())
    empreinte = hashlib.sha256(json.dumps([route, publics], sort_keys=True, default=str).encode()).hexdigest()
    if route == 'tester':   # simple vérification (aucun réglage modifié) : toujours refaite, jamais rejouée depuis un reçu
        try:
            return _out(tester(desk, owner, role, data))
        except Stop as refus:   # l'essai a répondu « non » : c'est son résultat (200), affiché comme une erreur par la page
            code = str(refus).split(':')[0]
            return _out({'error': code, 'message': message(code), 'etape': getattr(refus, 'etape', ''), 'noreload': True, 'resultat': 'echec'})
    ancien = recu(desk, owner, 'avocatfr/' + route, cle, empreinte)
    if ancien is not None:
        return _out(ancien)
    out = enregistrer(desk, env, owner, role, data)
    garder(desk, owner, 'avocatfr/' + route, cle, empreinte, out)
    return _out(out)


# ------------------------------------------------------------------------------------------------ présentation
def _champ(nom, libelle, valeur='', type_='text', aide='', auto='off', extra=''):
    ident = 'af106-%s-%s' % (nom, uuid.uuid4().hex[:8])
    return ('<div class="ui89-champ"><label for="%s">%s</label><input id="%s" name="%s" type="%s" value="%s" autocomplete="%s"%s%s>%s</div>') % (
        ident, e(libelle), ident, e(nom, quote=True), e(type_, quote=True), e(valeur, quote=True), e(auto, quote=True),
        (' aria-describedby="%s-a"' % ident) if aide else '', extra, ('<span class="ui89-aide" id="%s-a">%s</span>' % (ident, aide)) if aide else '')


def aide_mot_de_passe_html():
    """Aide en trois lignes (HTML sûr : seul le lien est du balisage, le texte est échappé)."""
    lien = '<a href="%s" target="_blank" rel="noopener noreferrer">auth.avocat.fr/deviceaccounts</a>' % e(LIEN_MOT_DE_PASSE, quote=True)
    lignes = (e('Ce n’est pas le mot de passe habituel de votre messagerie avocat.fr : c’est un mot de passe applicatif, propre à AxiorHub.'),
              e('Créez-le sur ') + lien + e(' (nouvel onglet) : connexion avec la Clé Avocat ou e-dentitas, « Ajouter un compte », nom d’appareil '
                                            '« AxiorHub Pilote », « Créer le compte ».'),
              e('Il n’est affiché qu’une fois : copiez-le aussitôt ici. Perdu, supprimez-le sur la même page et recréez-en un.'))
    return '<br>'.join(lignes)


def _tableau(i, m):
    lignes = ''.join('<tr><th scope="row">%s</th><td>%s</td><td>%s</td><td>%s</td></tr>' % (
        e(titre), e(r['host']), e(str(r['port'])), e(LIBELLES_SECURITE.get(r['security'], r['security']))) for titre, r in (('Réception (IMAP)', i), ('Envoi (SMTP)', m)))
    return ('<table class="ax-table af106-reglages"><caption>Réglages du CNB (lecture seule)</caption><thead><tr><th scope="col">Service</th>'
            '<th scope="col">Serveur</th><th scope="col">Port</th><th scope="col">Sécurité</th></tr></thead><tbody>%s</tbody></table>'
            '<p class="ax-muted">Identifiant de réception et d’envoi : l’adresse complète en @avocat.fr. Authentification : mot de passe normal '
            '(le mot de passe applicatif).</p>') % lignes


def _avances(s):
    """« Modifier les réglages avancés » : replié, désactivé tant que la case n'est pas cochée (préréglage du CNB par défaut)."""
    from .ui_components5689 import liste
    actuel_imap = s['imap_actuel'] if s['avocat'] and s['imap_actuel']['host'] else IMAP
    actuel_smtp = s['smtp_actuel'] if s['avocat'] and s['smtp_actuel']['host'] else SMTP
    different = s['avocat'] and (actuel_imap['host'] != IMAP['host'] or str(actuel_smtp['host']) != SMTP['host']
                                 or str(actuel_imap['port']) != str(IMAP['port']) or str(actuel_smtp['port']) != str(SMTP['port']))
    options = [(k, v) for k, v in LIBELLES_SECURITE.items()]
    champs = ''
    for nom, titre, actuel in (('imap', 'Réception (IMAP)', actuel_imap), ('smtp', 'Envoi (SMTP)', actuel_smtp)):
        champs += ('<fieldset class="ui89-champ af106-avance"><legend>%s</legend>%s%s%s</fieldset>') % (
            e(titre), _champ(nom + '_host', 'Serveur', str(actuel['host'])), _champ(nom + '_port', 'Port', str(actuel['port']), 'number', extra=' min="1" max="65535"'),
            liste(nom + '_security', 'Sécurité', options, str(actuel['security'] or 'ssl')))
    return ('<details class="gm97-guide af106-avances"%s><summary>Modifier les réglages avancés</summary><p class="ax-muted">Pour un cas particulier '
            'seulement : les réglages du CNB conviennent à toutes les adresses @avocat.fr.</p><div class="ui89-champ"><label class="m5-check">'
            '<input type="checkbox" name="avance"%s> <span>Utiliser ces réglages avancés à la place des réglages du CNB</span></label></div>%s</details>') % (
        ' open' if different else '', ' checked' if different else '', champs)


def section_html(desk, prefix, owner, role, env=None):
    """Carte « Adresse @avocat.fr (CNB) » : en tête des choix de messagerie (Paramètres › Connexions) et dans l'assistant de mise en service."""
    from .ui_components5689 import formulaire
    env = env or {}
    statut, phrase = etat_texte(desk)
    s = etat(desk)
    corps = '<p class="%s" role="status">%s</p>' % ({'ok': 'ax-ok', 'echec': 'ax-alert', 'a_tester': 'ax-alert'}.get(statut, 'ax-muted'), e(phrase))
    corps += ('<p>Confirmez ou corrigez votre adresse, saisissez votre mot de passe applicatif, puis « Tester ». Les serveurs de la messagerie du CNB '
              'sont déjà réglés.</p>')
    corps += _tableau(IMAP, SMTP)
    if role == 'administrateur':
        if s['avocat'] and s['adresse']:
            adresse, aide_adresse = s['adresse'], 'Adresse actuellement configurée.'
        else:
            adresse = proposer_adresse(nom_personne(desk, env, owner))
            aide_adresse = ('Proposition déduite de votre nom (minuscules, sans accents, espaces en tirets, apostrophes supprimées) : vérifiez-la, '
                            'notamment pour un nom composé ou une particule, et corrigez-la si besoin.' if adresse else
                            'Indiquez votre adresse complète, de la forme %s.' % FORME)
        avert = avertissement_domaine(adresse) if adresse else ''
        if avert:
            aide_adresse += ' ' + avert
        remplacer = ('<div class="ui89-champ"><label class="m5-check"><input type="checkbox" name="remplacer"> <span>Remplacer la messagerie actuelle '
                     'par cette adresse avocat.fr</span></label></div>') if s['autre_messagerie'] else ''
        corps += formulaire(PREFIXE + 'enregistrer',
                            gs._cache('revision', gs._revision_config(env, desk)) + gs._cache('cle', uuid.uuid4().hex)
                            + _champ('adresse', 'Adresse @avocat.fr', adresse, 'email', e(aide_adresse), 'username', ' placeholder="%s"' % e(FORME, quote=True))
                            + _champ('mot_de_passe', 'Mot de passe applicatif', '', 'password',
                                     aide_mot_de_passe_html() + ((e(' Laisser vide pour retester le mot de passe déjà enregistré pour cette adresse.'))
                                                                 if s['avocat'] and s['secret'] and s['smtp'] else ''), 'new-password')
                            + _avances(s) + remplacer
                            + '<div class="cx96-boutons"><button class="ax-btn ghost" type="submit" data-api="%stester">Tester</button> '
                              '<button class="ax-btn" type="submit">Enregistrer</button></div>' % e(PREFIXE, quote=True),
                            recharge=True, classe='af106-form')
        corps += ('<p class="ax-muted">« Tester » se connecte à la réception (connexion, liste des dossiers) puis à l’envoi (présentation, '
                  'authentification) sans envoyer aucun message. « Enregistrer » refait ce test, puis range le mot de passe dans le coffre.</p>')
    else:
        corps += '<p class="ax-muted">Réglage du cabinet : demandez à l’administrateur de le faire.</p>'
    return ('<section class="ax-card" id="avocatfr56106" aria-labelledby="avocatfr56106-t"><h2 id="avocatfr56106-t">Adresse @avocat.fr (CNB)</h2>%s'
            '</section>') % corps


def carte_guide(desk, env, prefix, admin=False, owner=''):
    """Carte de l'étape « Messagerie » du guide de mise en service (``guide5631.CARTES['avocatfr']``), en tête de l'étape."""
    from .web567 import actor
    owner = owner or actor(env or {})[0]
    return '<section class="g5631-sub af106-guide"><h3>Adresse @avocat.fr (CNB)</h3>%s</section>' % (
        section_html(desk, prefix, owner, 'administrateur' if admin else 'avocat', env))


carte_guide.avec_compte = True   # guide5631._carte : le compte de la personne est transmis
