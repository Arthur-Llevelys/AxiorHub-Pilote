"""5.6.103 (lot L) : liaison directe entre postes de travail sur le réseau local du cabinet, sans serveur.

Ce qui passe : messages et discussions (à deux, groupes, mentions ; réponses de Pilote), pièces jointes (25 Mo au plus, empreinte
SHA-256 vérifiée à l'arrivée), présence (en ligne, absent, hors ligne avec dernière vue), réglages partagés publiés par un poste
administrateur. Ce qui ne passe pas : dossiers, agenda, tâches (Nextcloud ou le serveur s'en chargent).

Décisions du cabinet appliquées ici :

- **Désactivée par défaut** (réglage ``liaison56103:actif``). Sans activation, ``poste.py`` ne lance pas le processus « liaison » et
  aucun port n'est ouvert au réseau. Le processus relit le réglage et s'arrête dès qu'il est retiré.
- **Écoute séparée** : son propre port TCP (47862 par défaut) sur UNE adresse privée du poste (jamais 0.0.0.0), qui ne sert ni
  l'interface ni l'API ; elle n'accepte que ce protocole, et seulement de postes appairés (hormis la poignée de main d'appairage,
  acceptée seulement pendant la validité d'un code affiché).
- **Plages privées seulement** : 10/8, 172.16/12, 192.168/16, fc00::/7, fe80::/10. Toute autre adresse est refusée avec un message
  clair (une adresse en 192 qui ne commence pas par 192.168 n'est pas privée). Découverte : diffusion UDP IPv4 limitée au lien (255.255.255.255, port 47863),
  annonce minimale ``{"axh": "liaison56103", "v": 1, "id", "nom", "port"}`` ; une annonce venue d'une adresse non privée est ignorée.

Schéma cryptographique (bibliothèque ``cryptography`` seulement ; aucune primitive maison) :

1. **Identité** de chaque poste : paire Ed25519 (signature) et paire X25519 statique (chiffrement de bout en bout des secrets des
   réglages partagés). Clés privées au coffre chiffré existant (``vault567``, fichier 0600, clé maîtresse 0600). Identifiant public
   du poste : 128 premiers bits de SHA-256(« axiorhub-liaison-56103 id » ‖ clé publique Ed25519), affiché en groupes de quatre.
2. **Appairage** par code à usage unique (8 chiffres, 10 minutes, 5 essais) affiché sur le poste B et saisi sur le poste A :
   SPAKE2 (RFC 9382) sur le groupe MODP 2048 bits de la RFC 3526 (générateur 2, sous-groupe d'ordre q = (p-1)/2), éléments M et N
   obtenus par hachage puis carré (logarithme discret inconnu de tous). Un attaquant actif ne teste qu'UN code par tentative ; les
   confirmations cA et cB (HMAC-SHA256 sur la transcription) prouvent la connaissance du code ; les identités (clés publiques Ed25519
   et X25519, nom, port) sont ensuite échangées sous ChaCha20-Poly1305 avec une clé dérivée de Ke, chacune signée sur la transcription.
   Chaque poste garde la clé publique de l'autre ; le code est effacé après la première confirmation réussie.
3. **Session** entre postes appairés (inspirée de SIGMA / TLS 1.3) : A → B ``bonjour`` (identifiant, clé Ed25519, X25519 éphémère,
   nonce 16 octets) ; B → A son ``bonjour`` signé sur H(« serveur » ‖ trame A ‖ trame B) ; A → B signature sur H(« client » ‖ trame A ‖
   trame B). Chacun vérifie que la clé présentée est CELLE gardée à l'appairage. Clés de session : HKDF-SHA256(X25519 éphémère,
   sel = nA ‖ nB, info = étiquette ‖ H(transcription)) → une clé par sens. Les clés éphémères donnent la confidentialité persistante ;
   une session enregistrée puis rejouée échoue (nouvelle clé éphémère de B, signature de A sur l'ancienne transcription).
4. **Trames** : longueur (4 octets) ‖ numéro de séquence (8 octets) ‖ ChaCha20-Poly1305(nonce = 0⁴ ‖ séquence, données associées =
   sens ‖ séquence). Le numéro doit être exactement le suivant attendu : une trame rejouée, réordonnée ou altérée ferme la session.
   Au-dessus, chaque message, réglage et pièce porte un identifiant global : un doublon applicatif est accusé sans être réinséré.
5. **Secrets des réglages partagés** : chiffrés pour CHAQUE poste destinataire (X25519 éphémère × X25519 statique du destinataire,
   HKDF-SHA256, ChaCha20-Poly1305, données associées = identifiant de diffusion ‖ nom du champ ‖ poste destinataire). Seul le
   chiffré est écrit dans la file d'envoi et dans les réglages reçus ; le clair n'existe qu'en mémoire, au moment de l'application,
   puis dans le coffre existant (``ai_gateway``, ``extensions364``, ``connexions5652``). Les identifiants personnels (mots de passe IMAP,
   jetons OAuth, sessions, abonnement ChatGPT) ne sont jamais publiés ni acceptés.

**Poste absent** : le message attend dans ``liaison56103_sortie`` (« en attente de remise ») et part au retour du destinataire ; c'est
la seule vraie limite sans serveur, affichée telle quelle. **Pilote** : c'est le poste de la personne qui l'interpelle qui fait tourner
l'IA (``messagerie5650`` → ``pilote_messagerie56102.apres_message`` sur ce poste) ; un message reçu par la liaison n'appelle jamais
Pilote, et la réponse de Pilote repart vers les autres postes comme un message.
"""
from datetime import datetime, timedelta, timezone
import base64
import hashlib
import hmac
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import socket
import threading
import time

from .common import Stop

VERSION = 1
PORT_TCP = 47862
PORT_UDP = 47863
MAX_PIECE = 25 * 1024 * 1024
MAX_TRAME = MAX_PIECE + 512 * 1024
MAX_CLAIR = 16 * 1024
MAX_ANNONCE = 512
CODE_DUREE = 600
CODE_ESSAIS = 5
HORS_LIGNE = 75            # secondes sans contact : « hors ligne »
BATTEMENT = 30             # contact de présence avec chaque poste appairé
DELAI_SOCKET = 15
MAX_SESSIONS = 16
ETIQUETTE = b'axiorhub-liaison-56103 v1'
PARTICIPANT = 'liaison:'   # participants5650.owner d'un poste relié : « liaison:<identifiant> »

REGLAGE_ACTIF = 'liaison56103:actif'
REGLAGE_NOM = 'liaison56103:nom'
REGLAGE_COMPTE = 'liaison56103:compte'
REGLAGE_PRESENCE = 'liaison56103:presence'
REGLAGE_ADRESSE = 'liaison56103:adresse'
REGLAGE_PORT = 'liaison56103:port'
REGLAGE_SERVICE = 'liaison56103:service'

PLAGES = tuple(ipaddress.ip_network(n) for n in ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16', 'fc00::/7', 'fe80::/10'))
ID_RE = re.compile(r'^[0-9a-f]{32}$')
GID_RE = re.compile(r'^[dgmrp]-[0-9a-f]{16,64}$')

SCHEMA = '''
CREATE TABLE IF NOT EXISTS liaison56103_postes(id TEXT PRIMARY KEY, nom TEXT NOT NULL DEFAULT '', cle_signature BLOB, cle_echange BLOB,
  adresse TEXT NOT NULL DEFAULT '', port INTEGER NOT NULL DEFAULT 0, source TEXT NOT NULL DEFAULT '', appaire_le TEXT NOT NULL DEFAULT '',
  retire INTEGER NOT NULL DEFAULT 0, contact_le TEXT NOT NULL DEFAULT '', presence TEXT NOT NULL DEFAULT '', presence_le TEXT NOT NULL DEFAULT '',
  erreur TEXT NOT NULL DEFAULT '', prochain_essai REAL NOT NULL DEFAULT 0, echecs INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS liaison56103_decouverts(id TEXT PRIMARY KEY, nom TEXT NOT NULL DEFAULT '', adresse TEXT NOT NULL,
  port INTEGER NOT NULL, vu_le TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS liaison56103_fils(thread INTEGER PRIMARY KEY, gid TEXT NOT NULL UNIQUE, genre TEXT NOT NULL,
  createur TEXT NOT NULL DEFAULT '', exporte_jusqu_a INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS liaison56103_messages(gid TEXT PRIMARY KEY, message INTEGER NOT NULL, thread INTEGER NOT NULL,
  origine TEXT NOT NULL, recu_le TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS liaison56103_messages_msg ON liaison56103_messages(message);
CREATE TABLE IF NOT EXISTS liaison56103_sortie(id INTEGER PRIMARY KEY AUTOINCREMENT, poste TEXT NOT NULL, gid TEXT NOT NULL,
  genre TEXT NOT NULL, thread INTEGER NOT NULL DEFAULT 0, charge TEXT NOT NULL, cree_le TEXT NOT NULL, tentatives INTEGER NOT NULL DEFAULT 0,
  etat TEXT NOT NULL DEFAULT 'en_attente', remis_le TEXT NOT NULL DEFAULT '', motif TEXT NOT NULL DEFAULT '', UNIQUE(poste, gid));
CREATE INDEX IF NOT EXISTS liaison56103_sortie_attente ON liaison56103_sortie(poste, etat, id);
CREATE TABLE IF NOT EXISTS liaison56103_pieces(id INTEGER PRIMARY KEY AUTOINCREMENT, message INTEGER NOT NULL DEFAULT 0,
  thread INTEGER NOT NULL, nom TEXT NOT NULL, taille INTEGER NOT NULL, sha256 TEXT NOT NULL, cree_le TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS liaison56103_reglages_recus(id INTEGER PRIMARY KEY AUTOINCREMENT, gid TEXT NOT NULL UNIQUE, poste TEXT NOT NULL,
  genre TEXT NOT NULL, cle TEXT NOT NULL, libelle TEXT NOT NULL DEFAULT '', valeurs TEXT NOT NULL DEFAULT '{}',
  secrets_chiffres TEXT NOT NULL DEFAULT '{}', etat TEXT NOT NULL DEFAULT 'en_attente', recu_le TEXT NOT NULL, decide_le TEXT NOT NULL DEFAULT '',
  decide_par TEXT NOT NULL DEFAULT '', motif TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS liaison56103_diffusions(gid TEXT PRIMARY KEY, genre TEXT NOT NULL, cle TEXT NOT NULL, libelle TEXT NOT NULL DEFAULT '',
  etats TEXT NOT NULL DEFAULT '{}', cree_le TEXT NOT NULL, par TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS liaison56103_recus(compte TEXT NOT NULL, route TEXT NOT NULL, cle TEXT NOT NULL, empreinte TEXT NOT NULL,
  reponse TEXT NOT NULL, cree_le TEXT NOT NULL, PRIMARY KEY(compte, route, cle));
'''


def _maintenant():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def _b64(data):
    return base64.b64encode(data).decode('ascii')


def _d64(text, taille=None):
    try:
        out = base64.b64decode(str(text or '').encode('ascii'), validate=True)
    except (ValueError, UnicodeError):
        raise Stop('trame_invalide') from None
    if taille is not None and len(out) != taille:
        raise Stop('trame_invalide')
    return out


def _json(obj):
    return json.dumps(obj, ensure_ascii=False, separators=(',', ':'), sort_keys=True).encode('utf-8')


def _lire_json(raw):
    try:
        obj = json.loads(raw.decode('utf-8'))
    except (ValueError, UnicodeError):
        raise Stop('trame_invalide') from None
    if not isinstance(obj, dict):
        raise Stop('trame_invalide')
    return obj


def nettoyer_nom(value, defaut='Poste'):
    value = re.sub(r'[\x00-\x1f\x7f<>]', '', str(value or ''))
    value = re.sub(r'\s+', ' ', value).strip()[:80]
    return value or defaut


def ensure_schema(desk):
    """Tables de la liaison, créées au démarrage (``Desk._prepare``), jamais pendant une lecture."""
    desk.db.executescript(SCHEMA)
    desk.db.commit()


def est_active(desk):
    try:
        return desk.settings(REGLAGE_ACTIF, False) is True
    except Exception:   # noqa: BLE001 — réglage illisible : la liaison reste fermée
        return False


def active_dans(config_path):
    """Pour ``poste.py`` (superviseur) : la liaison est-elle activée ? Lecture seule de la base, sans ouvrir ``Desk`` ; toute erreur = non."""
    import sqlite3
    try:
        cfg = json.loads(Path(config_path).read_text(encoding='utf-8'))
        base = Path(cfg['state_dir']) / 'desk.sqlite3'
        if not base.is_file():
            return False
        db = sqlite3.connect(base.resolve().as_uri() + '?mode=ro', uri=True, timeout=2)
        try:
            row = db.execute('SELECT value FROM settings WHERE key=?', (REGLAGE_ACTIF,)).fetchone()
        finally:
            db.close()
        return bool(row) and json.loads(row[0]) is True
    except Exception:   # noqa: BLE001
        return False


# ------------------------------------------------------------------------------------------------ adresses
def verifier_adresse(texte, boucle=False):
    """Adresse d'un poste du cabinet : IPv4 ou IPv6 d'une plage privée seulement. ``boucle`` (127.0.0.1, ::1) : essais seulement,
    jamais depuis l'interface. Rend l'adresse normalisée ou lève ``adresse_non_privee:<adresse>`` / ``adresse_invalide``."""
    brut = str(texte or '').strip()
    if brut.startswith('[') and brut.endswith(']'):
        brut = brut[1:-1]
    if not brut or len(brut) > 64:
        raise Stop('adresse_invalide')
    try:
        adresse = ipaddress.ip_address(brut)
    except ValueError:
        raise Stop('adresse_invalide') from None
    simple = adresse.ipv4_mapped if getattr(adresse, 'ipv4_mapped', None) else adresse
    if boucle and simple.is_loopback:
        return str(adresse)
    if not any(simple.version == plage.version and simple in plage for plage in PLAGES):
        raise Stop('adresse_non_privee:' + brut[:64])
    return str(adresse)


def verifier_port(value):
    try:
        port = int(value)
    except (TypeError, ValueError):
        raise Stop('port_invalide') from None
    if not 1024 <= port <= 65535:
        raise Stop('port_invalide')
    return port


def message_adresse(code):
    """Texte clair pour une adresse refusée (cas réel signalé par l'avocat : une adresse en 192 sans le « 168 »). Aucune adresse
    n'est recopiée dans le message."""
    _, _, brut = str(code).partition(':')
    aide = ''
    if re.match(r'^192\.(?!168\.)\d', brut):
        aide = (' Une adresse du réseau local qui commence par 192 se poursuit par 168 (192.168…) : vérifiez qu’aucun chiffre ne manque.')
    return ('Cette adresse n’appartient pas à un réseau local privé (10…, 172.16 à 172.31…, 192.168… ; en IPv6 fc00::/7 et fe80::/10) : '
            'la liaison la refuse. Relevez l’adresse affichée dans Paramètres sur l’autre poste.' + aide)


def adresses_locales():
    """Adresses privées de ce poste (candidates à l'écoute) ; aucun paquet n'est émis (UDP « connect » sans envoi)."""
    trouvees = []
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None):
            trouvees.append(info[4][0])
    except OSError:
        pass
    for cible in ('10.255.255.254', '172.31.255.254', '192.168.255.254'):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.connect((cible, 9))
                trouvees.append(s.getsockname()[0])
        except OSError:
            pass
    out = []
    for a in trouvees:
        try:
            a = verifier_adresse(a.split('%')[0])
        except Stop:
            continue
        if a not in out:
            out.append(a)
    return out


# ------------------------------------------------------------------------------------------------ identité du poste
def _crypto():
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
        from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.kdf.hkdf import HKDF
        from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
        from cryptography.exceptions import InvalidSignature, InvalidTag
    except ImportError:
        raise Stop('chiffrement_indisponible') from None
    return {'Ed': Ed25519PrivateKey, 'EdPub': Ed25519PublicKey, 'X': X25519PrivateKey, 'XPub': X25519PublicKey, 'hashes': hashes,
            'ser': serialization, 'HKDF': HKDF, 'AEAD': ChaCha20Poly1305, 'InvalidSignature': InvalidSignature, 'InvalidTag': InvalidTag}


def _brut_public(cle):
    k = _crypto()['ser']
    return cle.public_bytes(k.Encoding.Raw, k.PublicFormat.Raw)


def _brut_prive(cle):
    k = _crypto()['ser']
    return cle.private_bytes(k.Encoding.Raw, k.PrivateFormat.Raw, k.NoEncryption())


def empreinte(cle_publique):
    return hashlib.sha256(b'axiorhub-liaison-56103 id' + bytes(cle_publique)).hexdigest()[:32]


def empreinte_lisible(ident):
    return ' '.join(str(ident)[i:i + 4] for i in range(0, len(str(ident)), 4)).upper()


class Identite:
    def __init__(self, signature, echange):
        self._signature, self._echange = signature, echange
        self.cle_signature = _brut_public(signature.public_key())
        self.cle_echange = _brut_public(echange.public_key())
        self.id = empreinte(self.cle_signature)

    def signer(self, donnees):
        return self._signature.sign(donnees)

    def echanger(self, cle_publique):
        return self._echange.exchange(_crypto()['XPub'].from_public_bytes(cle_publique))


def _chemin_identite(state_dir):
    return Path(state_dir) / 'vault' / 'liaison56103' / 'identite.secret'


_IDENT_LOCK = threading.Lock()


def identite(state_dir, creer=True):
    """Clés du poste, au coffre chiffré (``vault567`` : fichier 0600 chiffré par la clé maîtresse 0600). Création sous verrou de fichier
    (l'interface et le processus de liaison ne créent jamais deux identités différentes)."""
    k = _crypto()
    chemin = _chemin_identite(state_dir)
    from .common import read_secret
    with _IDENT_LOCK:
        if not chemin.is_file():
            if not creer:
                raise Stop('identite_liaison_absente')
            from .portable import fcntl
            chemin.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            with open(chemin.parent / 'identite.lock', 'a') as verrou:
                fcntl.flock(verrou, fcntl.LOCK_EX)
                try:
                    if not chemin.is_file():
                        from .vault567 import write
                        sig, ech = k['Ed'].generate(), k['X'].generate()
                        write(chemin, 'v1:%s:%s' % (_b64(_brut_prive(sig)), _b64(_brut_prive(ech))))
                finally:
                    fcntl.flock(verrou, fcntl.LOCK_UN)
        valeur = read_secret(str(chemin))
    parts = valeur.split(':')
    if len(parts) != 3 or parts[0] != 'v1':
        raise Stop('identite_liaison_illisible')
    return Identite(k['Ed'].from_private_bytes(_d64(parts[1], 32)), k['X'].from_private_bytes(_d64(parts[2], 32)))


def _verifier(cle_publique, signature, donnees):
    """Signature Ed25519 valide ? (garde-fou : une clé publique connue ne suffit jamais sans la clé privée)."""
    k = _crypto()
    try:
        k['EdPub'].from_public_bytes(bytes(cle_publique)).verify(bytes(signature), bytes(donnees))
        return True
    except (k['InvalidSignature'], ValueError, TypeError):
        return False


def _h(*parts):
    h = hashlib.sha256()
    for p in parts:
        p = bytes(p)
        h.update(len(p).to_bytes(4, 'big') + p)
    return h.digest()


def _hkdf(ikm, sel, info, longueur):
    k = _crypto()
    return k['HKDF'](algorithm=k['hashes'].SHA256(), length=longueur, salt=sel, info=info).derive(ikm)


# ------------------------------------------------------------------------------------------------ trames
def _lire(sock, n):
    buf = bytearray()
    while len(buf) < n:
        part = sock.recv(min(n - len(buf), 1 << 20))
        if not part:
            raise Stop('liaison_interrompue')
        buf += part
    return bytes(buf)


def _envoyer_brut(sock, data):
    sock.sendall(len(data).to_bytes(4, 'big') + data)


def _lire_brut(sock, maxi):
    n = int.from_bytes(_lire(sock, 4), 'big')
    if n < 1 or n > maxi:
        raise Stop('trame_invalide')
    return _lire(sock, n)


def _nonce(seq):
    return b'\x00\x00\x00\x00' + seq


def _chiffrer(aead, nonce, clair, associees):
    return aead.encrypt(nonce, clair, associees)


def _dechiffrer(aead, nonce, chiffre, associees):
    return aead.decrypt(nonce, chiffre, associees)


def _sequence_attendue(recu, attendu):
    """Anti-rejeu : seule la trame suivante attendue est acceptée."""
    return recu == attendu


class Canal:
    """Trames chiffrées et authentifiées d'une session (une clé et un compteur par sens)."""

    def __init__(self, sock, cle_envoi, cle_reception, sens_envoi, sens_reception):
        aead = _crypto()['AEAD']
        self.sock = sock
        self._envoi, self._reception = aead(cle_envoi), aead(cle_reception)
        self.sens_envoi, self.sens_reception = sens_envoi, sens_reception
        self.seq_envoi = self.seq_reception = 0

    def envoyer(self, obj, donnees=b''):
        entete = _json(obj)
        clair = len(entete).to_bytes(4, 'big') + entete + bytes(donnees or b'')
        seq = self.seq_envoi.to_bytes(8, 'big')
        self.seq_envoi += 1
        _envoyer_brut(self.sock, seq + _chiffrer(self._envoi, _nonce(seq), clair, self.sens_envoi + seq))

    def recevoir(self):
        brut = _lire_brut(self.sock, MAX_TRAME)
        if len(brut) < 8 + 16:
            raise Stop('trame_invalide')
        seq = brut[:8]
        numero = int.from_bytes(seq, 'big')
        if not _sequence_attendue(numero, self.seq_reception):
            raise Stop('trame_rejouee')
        try:
            clair = _dechiffrer(self._reception, _nonce(seq), brut[8:], self.sens_reception + seq)
        except _crypto()['InvalidTag']:
            raise Stop('trame_alteree') from None
        self.seq_reception = numero + 1
        if len(clair) < 4:
            raise Stop('trame_invalide')
        n = int.from_bytes(clair[:4], 'big')
        if n > len(clair) - 4 or n > MAX_CLAIR * 64:
            raise Stop('trame_invalide')
        return _lire_json(clair[4:4 + n]), clair[4 + n:]


# ------------------------------------------------------------------------------------------------ session entre postes appairés
def _poste(desk, ident):
    if not ID_RE.fullmatch(str(ident or '')):
        return None
    return desk.db.execute('SELECT * FROM liaison56103_postes WHERE id=? AND retire=0 AND cle_signature IS NOT NULL', (ident,)).fetchone()


def ouvrir_client(sock, ident, attendu, nom, port):
    """Côté appelant : session avec le poste ``attendu`` (ligne de ``liaison56103_postes``) ; rend un ``Canal`` ou lève ``Stop``."""
    k = _crypto()
    eph = k['X'].generate()
    n_a = secrets.token_bytes(16)
    t1 = _json({'t': 'bonjour', 'v': VERSION, 'mode': 'liaison', 'id': ident.id, 'cle': _b64(ident.cle_signature),
                'eph': _b64(_brut_public(eph.public_key())), 'n': _b64(n_a), 'nom': nettoyer_nom(nom), 'port': int(port or 0)})
    _envoyer_brut(sock, t1)
    r = _lire_json(_lire_brut(sock, MAX_CLAIR))
    if r.get('t') == 'refus':
        raise Stop('poste_refuse:' + re.sub(r'[^a-z_]', '', str(r.get('motif') or ''))[:40])
    corps = str(r.get('corps') or '').encode('utf-8')
    b = _lire_json(corps)
    cle_b = _d64(b.get('cle'), 32)
    if b.get('id') != attendu['id'] or bytes(attendu['cle_signature'] or b'') != cle_b or empreinte(cle_b) != attendu['id']:
        raise Stop('poste_inattendu')
    if not _verifier(cle_b, _d64(r.get('sig'), 64), _h(b'serveur', t1, corps)):
        raise Stop('signature_refusee')
    _envoyer_brut(sock, _json({'t': 'preuve', 'sig': _b64(ident.signer(_h(b'client', t1, corps)))}))
    partage = eph.exchange(k['XPub'].from_public_bytes(_d64(b.get('eph'), 32)))
    cles = _hkdf(partage, n_a + _d64(b.get('n'), 16), ETIQUETTE + _h(t1, corps), 64)
    canal = Canal(sock, cles[:32], cles[32:], b'cs', b'sc')
    pret, _ = canal.recevoir()
    if pret.get('t') != 'pret':
        raise Stop('poste_refuse')
    canal.distant = {'nom': nettoyer_nom(b.get('nom')), 'port': b.get('port')}
    return canal


def ouvrir_serveur(sock, ident, desk, t1, h1, nom, port):
    """Côté appelé, mode « liaison » : refus neutre pour un poste non appairé, retiré, ou dont la clé n'est pas celle gardée."""
    k = _crypto()
    try:
        cle_a = _d64(h1.get('cle'), 32)
    except Stop:
        cle_a = b''
    poste = _poste(desk, h1.get('id'))
    if not poste or not cle_a or empreinte(cle_a) != poste['id'] or bytes(poste['cle_signature']) != cle_a:
        _envoyer_brut(sock, _json({'t': 'refus', 'motif': 'poste_non_appaire'}))
        raise Stop('poste_non_appaire')
    eph = k['X'].generate()
    n_b = secrets.token_bytes(16)
    corps = _json({'t': 'bonjour', 'v': VERSION, 'id': ident.id, 'cle': _b64(ident.cle_signature), 'eph': _b64(_brut_public(eph.public_key())),
                   'n': _b64(n_b), 'nom': nettoyer_nom(nom), 'port': int(port or 0)})
    _envoyer_brut(sock, _json({'corps': corps.decode('utf-8'), 'sig': _b64(ident.signer(_h(b'serveur', t1, corps)))}))
    preuve = _lire_json(_lire_brut(sock, MAX_CLAIR))
    if not _verifier(cle_a, _d64(preuve.get('sig'), 64), _h(b'client', t1, corps)):
        raise Stop('signature_refusee')
    partage = eph.exchange(k['XPub'].from_public_bytes(_d64(h1.get('eph'), 32)))
    cles = _hkdf(partage, _d64(h1.get('n'), 16) + n_b, ETIQUETTE + _h(t1, corps), 64)
    canal = Canal(sock, cles[32:], cles[:32], b'sc', b'cs')
    canal.envoyer({'t': 'pret'})
    canal.poste = poste
    return canal


# ------------------------------------------------------------------------------------------------ appairage (SPAKE2, RFC 9382)
P = int(''.join('''
FFFFFFFF FFFFFFFF C90FDAA2 2168C234 C4C6628B 80DC1CD1 29024E08 8A67CC74 020BBEA6 3B139B22 514A0879 8E3404DD
EF9519B3 CD3A431B 302B0A6D F25F1437 4FE1356D 6D51C245 E485B576 625E7EC6 F44C42E9 A637ED6B 0BFF5CB6 F406B7ED
EE386BFB 5A899FA5 AE9F2411 7C4B1FE6 49286651 ECE45B3D C2007CB8 A163BF05 98DA4836 1C55D39A 69163FA8 FD24CF5F
83655D23 DCA3AD96 1C62F356 208552BB 9ED52907 7096966D 670C354E 4ABC9804 F1746C08 CA18217C 32905E46 2E36CE3B
E39E772C 180E8603 9B2783A2 EC07A28F B5C55DF0 6F4C52C9 DE2BCBF6 95581718 3995497C EA956AE5 15D22618 98FA0510
15728E5A 8AACAA68 FFFFFFFF FFFFFFFF'''.split()), 16)   # RFC 3526, groupe 14 (premier sûr de 2048 bits)
Q = (P - 1) // 2
G = 2
TAILLE = 256


def _element(etiquette):
    """Élément du sous-groupe d'ordre q sans logarithme connu : hachage étendu puis carré modulo p."""
    flux = b''.join(hashlib.sha512(etiquette + i.to_bytes(4, 'big')).digest() for i in range(5))
    return pow(int.from_bytes(flux, 'big') % P, 2, P)


M = _element(b'axiorhub-liaison-56103 SPAKE2 M')
N = _element(b'axiorhub-liaison-56103 SPAKE2 N')


def _membre(v):
    return isinstance(v, int) and 1 < v < P - 1 and pow(v, Q, P) == 1


def _w(code):
    return int.from_bytes(hashlib.sha512(b'axiorhub-liaison-56103 code ' + str(code).encode('ascii')).digest(), 'big') % Q


def _octets(v):
    return int(v).to_bytes(TAILLE, 'big')


def _entier(texte):
    try:
        v = int(str(texte or ''), 16)
    except ValueError:
        raise Stop('trame_invalide') from None
    if not _membre(v):
        raise Stop('element_invalide')
    return v


def _cles_pake(p_a, p_b, k_partage, w):
    tt = _h(b'A', b'B', _octets(p_a), _octets(p_b), _octets(k_partage), _octets(w))
    brut = hashlib.sha512(tt).digest()
    ke, ka = brut[:32], brut[32:]
    kc = _hkdf(ka, None, b'ConfirmationKeys', 64)
    return {'tt': tt, 'ca': hmac.new(kc[:32], tt, 'sha256').digest(), 'cb': hmac.new(kc[32:], tt, 'sha256').digest(),
            'canal': _hkdf(ke, None, ETIQUETTE + b' appairage', 64)}


def normaliser_code(code):
    chiffres = re.sub(r'[\s\-.]', '', str(code or ''))
    if not re.fullmatch(r'[0-9]{8}', chiffres):
        raise Stop('code_appairage_invalide')
    return chiffres


def _fichier_code(state_dir):
    return Path(state_dir) / 'liaison56103' / 'code-appairage.json'


_CODE_LOCK = threading.Lock()


def _ecrire_prive(chemin, data):
    chemin.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = chemin.with_name(chemin.name + '.' + secrets.token_hex(4) + '.tmp')
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as f:
        json.dump(data, f)
    os.replace(tmp, chemin)


def nouveau_code(desk, duree=CODE_DUREE):
    """Code à usage unique (8 chiffres) affiché sur ce poste : fichier privé 0600, valable ``duree`` secondes, ``CODE_ESSAIS`` essais."""
    code = '%08d' % secrets.randbelow(10 ** 8)
    with _CODE_LOCK:
        _ecrire_prive(_fichier_code(desk.c['state_dir']), {'code': code, 'expire': time.time() + duree, 'essais': CODE_ESSAIS})
    return {'code': code[:4] + '-' + code[4:], 'expire': time.time() + duree}


def code_actif(state_dir):
    """Code en cours (lecture seule) : ``{'code', 'expire', 'essais'}`` ou None."""
    try:
        data = json.loads(_fichier_code(state_dir).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or float(data.get('expire') or 0) < time.time() or int(data.get('essais') or 0) <= 0:
        return None
    return data


def _consommer_code(state_dir, reussi):
    """Échec : un essai de moins (code effacé au dernier). Réussite : code effacé (usage unique)."""
    chemin = _fichier_code(state_dir)
    with _CODE_LOCK:
        data = code_actif(state_dir)
        if reussi or not data or int(data['essais']) <= 1:
            try:
                chemin.unlink()
            except OSError:
                pass
            return
        data['essais'] = int(data['essais']) - 1
        _ecrire_prive(chemin, data)


def _carte(ident, nom, port, tt, role):
    corps = {'id': ident.id, 'cle': _b64(ident.cle_signature), 'echange': _b64(ident.cle_echange), 'nom': nettoyer_nom(nom), 'port': int(port or 0)}
    corps['sig'] = _b64(ident.signer(_h(b'identite ' + role, tt, _json({k: v for k, v in corps.items() if k != 'sig'}))))
    return corps


def _lire_carte(carte, tt, role):
    cle = _d64(carte.get('cle'), 32)
    echange = _d64(carte.get('echange'), 32)
    sans = {k: carte.get(k) for k in ('id', 'cle', 'echange', 'nom', 'port')}
    if carte.get('id') != empreinte(cle) or not _verifier(cle, _d64(carte.get('sig'), 64), _h(b'identite ' + role, tt, _json(sans))):
        raise Stop('signature_refusee')
    return {'id': carte['id'], 'cle_signature': cle, 'cle_echange': echange, 'nom': nettoyer_nom(carte.get('nom')), 'port': carte.get('port')}


def _enregistrer_poste(desk, carte, adresse, port, source):
    try:
        port = verifier_port(port)
    except Stop:
        port = PORT_TCP
    desk.db.execute('INSERT INTO liaison56103_postes(id,nom,cle_signature,cle_echange,adresse,port,source,appaire_le,retire,contact_le) '
                    'VALUES(?,?,?,?,?,?,?,?,0,?) ON CONFLICT(id) DO UPDATE SET nom=excluded.nom,cle_signature=excluded.cle_signature,'
                    'cle_echange=excluded.cle_echange,adresse=excluded.adresse,port=excluded.port,source=excluded.source,'
                    "appaire_le=excluded.appaire_le,retire=0,erreur='',echecs=0,prochain_essai=0,contact_le=excluded.contact_le",
                    (carte['id'], carte['nom'], carte['cle_signature'], carte['cle_echange'], adresse, port, source, _maintenant(), _maintenant()))
    desk.db.commit()
    desk.audit('liaison56103_poste_appaire', {'poste': carte['id'], 'nom': carte['nom'], 'source': source})


def appairer(desk, adresse, port, code, boucle=False, delai=DELAI_SOCKET):
    """Poste A (où le code est saisi) : appelle le poste B, SPAKE2 avec le code, échange signé des identités, enregistrement de B."""
    if not est_active(desk):
        raise Stop('liaison_desactivee')
    adresse = verifier_adresse(adresse, boucle)
    port = verifier_port(port or PORT_TCP)
    w = _w(normaliser_code(code))
    ident = identite(desk.c['state_dir'])
    x = secrets.randbelow(Q - 1) + 1
    p_a = pow(G, x, P) * pow(M, w, P) % P
    try:
        sock = socket.create_connection((adresse, port), timeout=delai)
    except OSError:
        raise Stop('poste_injoignable') from None
    try:
        sock.settimeout(delai)
        _envoyer_brut(sock, _json({'t': 'bonjour', 'v': VERSION, 'mode': 'appairage', 'pA': '%x' % p_a}))
        r = _lire_json(_lire_brut(sock, MAX_CLAIR))
        if r.get('t') == 'refus':
            raise Stop(re.sub(r'[^a-z_]', '', str(r.get('motif') or 'poste_refuse'))[:40] or 'poste_refuse')
        p_b = _entier(r.get('pB'))
        k_partage = pow(p_b * pow(N, -w, P) % P, x, P)
        cles = _cles_pake(p_a, p_b, k_partage, w)
        _envoyer_brut(sock, _json({'t': 'confirmation', 'cA': cles['ca'].hex()}))
        r = _lire_json(_lire_brut(sock, MAX_CLAIR))
        if r.get('t') == 'refus':
            raise Stop(re.sub(r'[^a-z_]', '', str(r.get('motif') or 'code_refuse'))[:40] or 'code_refuse')
        if not hmac.compare_digest(str(r.get('cB') or ''), cles['cb'].hex()):
            raise Stop('code_refuse')   # le poste appelé ne connaît pas le code : ce n'est pas le bon poste
        canal = Canal(sock, cles['canal'][:32], cles['canal'][32:], b'ab', b'ba')
        canal.envoyer({'t': 'identite', **_carte(ident, nom_du_poste(desk), port_ecoute(desk), cles['tt'], b'A')})
        reponse, _ = canal.recevoir()
        carte = _lire_carte(reponse, cles['tt'], b'B')
        if carte['id'] == ident.id:
            raise Stop('poste_identique')
        _enregistrer_poste(desk, carte, adresse, carte.get('port') or port, 'appairage')
        return {'id': carte['id'], 'nom': carte['nom']}
    except OSError:
        raise Stop('poste_injoignable') from None
    finally:
        try:
            sock.close()
        except OSError:
            pass


def _serveur_appairage(sock, ident, desk, h1, adresse, nom, port):
    """Poste B (qui affiche le code) : refus sans code actif ; un essai décompté par confirmation fausse."""
    state_dir = desk.c['state_dir']
    actif = code_actif(state_dir)
    if not actif:
        _envoyer_brut(sock, _json({'t': 'refus', 'motif': 'aucun_code_actif'}))
        raise Stop('aucun_code_actif')
    w = _w(actif['code'])
    p_a = _entier(h1.get('pA'))
    y = secrets.randbelow(Q - 1) + 1
    p_b = pow(G, y, P) * pow(N, w, P) % P
    k_partage = pow(p_a * pow(M, -w, P) % P, y, P)
    cles = _cles_pake(p_a, p_b, k_partage, w)
    _envoyer_brut(sock, _json({'t': 'pake', 'pB': '%x' % p_b}))
    r = _lire_json(_lire_brut(sock, MAX_CLAIR))
    if not hmac.compare_digest(str(r.get('cA') or ''), cles['ca'].hex()):
        _consommer_code(state_dir, False)
        desk.audit('liaison56103_code_refuse', {'adresse': adresse})
        _envoyer_brut(sock, _json({'t': 'refus', 'motif': 'code_refuse'}))
        raise Stop('code_refuse')
    _consommer_code(state_dir, True)
    _envoyer_brut(sock, _json({'t': 'confirmation', 'cB': cles['cb'].hex()}))
    canal = Canal(sock, cles['canal'][32:], cles['canal'][:32], b'ba', b'ab')
    demande, _ = canal.recevoir()
    carte = _lire_carte(demande, cles['tt'], b'A')
    if carte['id'] == ident.id:
        raise Stop('poste_identique')
    _enregistrer_poste(desk, carte, adresse, carte.get('port'), 'appairage')   # avant la réponse : A ne se croit jamais appairé seul
    canal.envoyer({'t': 'identite', **_carte(ident, nom, port, cles['tt'], b'B')})


# ------------------------------------------------------------------------------------------------ réglages du poste
def nom_du_poste(desk):
    return nettoyer_nom(desk.settings(REGLAGE_NOM, '') or '', 'Poste AxiorHub')


def compte_du_poste(desk):
    return str(desk.settings(REGLAGE_COMPTE, '') or 'cabinet')[:200]


def port_ecoute(desk):
    try:
        return verifier_port(desk.settings(REGLAGE_PORT, PORT_TCP) or PORT_TCP)
    except Stop:
        return PORT_TCP


def presence_locale(desk):
    return 'absent' if desk.settings(REGLAGE_PRESENCE, 'en_ligne') == 'absent' else 'en_ligne'


def activer(desk, owner, nom_compte, actif):
    """Active ou désactive la liaison de CE poste ; à l'activation, l'identité est créée et le compte du poste retenu."""
    if actif:
        ident = identite(desk.c['state_dir'])
        if not desk.settings(REGLAGE_COMPTE, ''):
            desk.setting(REGLAGE_COMPTE, str(owner or 'cabinet')[:200])
        if not desk.settings(REGLAGE_NOM, ''):
            desk.setting(REGLAGE_NOM, nettoyer_nom('Poste de ' + str(nom_compte or owner or 'cabinet')))
    desk.setting(REGLAGE_ACTIF, bool(actif))
    desk.audit('liaison56103_activation', {'actif': bool(actif), 'par': str(owner or '')[:80]})
    return {'actif': bool(actif), 'id': ident.id if actif else ''}


def postes(desk, actifs=True):
    sql = 'SELECT * FROM liaison56103_postes' + (' WHERE retire=0 AND cle_signature IS NOT NULL' if actifs else '') + ' ORDER BY nom, id'
    return [dict(r) for r in desk.db.execute(sql)]


def presence_de(poste, maintenant=None):
    """'en_ligne', 'absent' ou 'hors_ligne' (selon le dernier contact authentifié)."""
    maintenant = maintenant or datetime.now(timezone.utc)
    try:
        vu = datetime.fromisoformat(poste['contact_le'])
    except (TypeError, ValueError):
        return 'hors_ligne'
    if (maintenant - vu).total_seconds() > HORS_LIGNE:
        return 'hors_ligne'
    return 'absent' if poste.get('presence') == 'absent' else 'en_ligne'


def retirer(desk, ident, par=''):
    row = desk.db.execute('SELECT * FROM liaison56103_postes WHERE id=? AND retire=0', (str(ident or ''),)).fetchone()
    if not row:
        raise Stop('poste_absent')
    desk.db.execute('UPDATE liaison56103_postes SET retire=1,cle_signature=NULL,cle_echange=NULL WHERE id=?', (row['id'],))
    desk.db.execute("UPDATE liaison56103_sortie SET etat='annule',motif='poste_retire' WHERE poste=? AND etat='en_attente'", (row['id'],))
    desk.db.commit()
    desk.audit('liaison56103_poste_retire', {'poste': row['id'], 'nom': row['nom'], 'par': str(par)[:80]})
    return {'id': row['id'], 'nom': row['nom']}


def membres_relies(desk):
    """Postes appairés présentés comme membres de la messagerie (``liaison:<id>``), pour la Messagerie instantanée seulement."""
    from .affichage5696 import table_presente
    if not table_presente(desk.db, 'liaison56103_postes'):
        return {}
    return {PARTICIPANT + r['id']: {'owner': PARTICIPANT + r['id'], 'name': r['nom'] or 'Poste relié', 'role': 'poste relié', 'source': 'liaison'}
            for r in desk.db.execute('SELECT id, nom FROM liaison56103_postes WHERE retire=0 AND cle_signature IS NOT NULL')}


def augmenter(desk, members):
    try:
        if not est_active(desk):
            return members
        return {**members, **membres_relies(desk)}
    except Exception:   # noqa: BLE001 — la messagerie locale ne dépend pas de la liaison
        return members


# ------------------------------------------------------------------------------------------------ messages : sortie
def _mx():
    from . import messagerie5650
    return messagerie5650


def _ids_participants(desk, tid):
    return [r['owner'] for r in desk.db.execute("SELECT owner FROM participants5650 WHERE thread=? AND left_at=''", (tid,))]


def _gid_direct(a, b):
    return 'd-' + hashlib.sha256(('|'.join(sorted((a, b)))).encode('ascii')).hexdigest()[:32]


def _fil_lie(desk, row, moi):
    """Correspondance globale d'un fil local relié (créée à la première sortie) ; None si le fil n'a aucun poste relié."""
    lien = desk.db.execute('SELECT * FROM liaison56103_fils WHERE thread=?', (row['id'],)).fetchone()
    if lien:
        return lien
    relies = [o[len(PARTICIPANT):] for o in _ids_participants(desk, row['id']) if o.startswith(PARTICIPANT)]
    if not relies or row['kind'] not in ('direct', 'groupe'):
        return None
    if row['kind'] == 'direct':
        gid = _gid_direct(moi, relies[0])
    else:
        gid = 'g-' + secrets.token_hex(16)
    desk.db.execute('INSERT OR IGNORE INTO liaison56103_fils(thread,gid,genre,createur,exporte_jusqu_a) VALUES(?,?,?,?,0)',
                    (row['id'], gid, row['kind'], moi))
    return desk.db.execute('SELECT * FROM liaison56103_fils WHERE thread=?', (row['id'],)).fetchone()


def _membres_fil(desk, tid, moi):
    noms = {r['id']: r['nom'] for r in desk.db.execute('SELECT id, nom FROM liaison56103_postes')}
    out, vus = [], set()
    for o in _ids_participants(desk, tid):
        if o == 'pilote':
            continue
        pid = o[len(PARTICIPANT):] if o.startswith(PARTICIPANT) else moi
        if pid in vus:
            continue
        vus.add(pid)
        out.append({'id': pid, 'nom': nom_du_poste(desk) if pid == moi else (noms.get(pid) or 'Poste relié')})
    return out


PIECE_LIEN = re.compile(r'^/api440/m56103/liaison/piece\?id=([0-9]{1,12})$')


def exporter(desk, ident=None, limite=200):
    """Met en file d'envoi les nouveaux messages LOCAUX (personnes du poste, réponses de Pilote) des fils reliés ; rend leur nombre.
    Les messages reçus d'un autre poste ne repartent jamais (pas d'écho)."""
    from .affichage5696 import table_presente
    if not (table_presente(desk.db, 'fils5650') and table_presente(desk.db, 'participants5650')):
        return 0
    ident = ident or identite(desk.c['state_dir'], creer=False)
    moi = ident.id
    actifs = {r['id'] for r in desk.db.execute('SELECT id FROM liaison56103_postes WHERE retire=0 AND cle_signature IS NOT NULL')}
    fils = desk.db.execute("SELECT DISTINCT f.* FROM fils5650 f JOIN participants5650 p ON p.thread=f.id WHERE f.kind IN ('direct','groupe') "
                           "AND p.owner LIKE 'liaison:%' AND p.left_at=''").fetchall()
    total = 0
    for row in fils:
        lien = _fil_lie(desk, row, moi)
        if not lien:
            continue
        cibles = [o[len(PARTICIPANT):] for o in _ids_participants(desk, row['id']) if o.startswith(PARTICIPANT)]
        cibles = [c for c in cibles if c in actifs and c != moi]
        nouveaux = desk.db.execute("SELECT * FROM messages5650 m WHERE thread=? AND id>? AND deleted=0 AND kind IN ('message','ia') "
                                   "AND author NOT LIKE 'liaison:%' AND NOT EXISTS (SELECT 1 FROM liaison56103_messages l WHERE l.message=m.id) "
                                   'ORDER BY id LIMIT ?', (row['id'], lien['exporte_jusqu_a'], limite)).fetchall()
        if not nouveaux:
            continue
        fil = {'gid': lien['gid'], 'genre': lien['genre'], 'titre': row['title'] if row['kind'] == 'groupe' else '',
               'createur': lien['createur'], 'membres': _membres_fil(desk, row['id'], moi)}
        for m in nouveaux:
            gid = 'm-' + secrets.token_hex(16)
            piece = None
            match = PIECE_LIEN.match(m['link'] or '')
            if match:
                p = desk.db.execute('SELECT * FROM liaison56103_pieces WHERE id=? AND thread=?', (int(match.group(1)), row['id'])).fetchone()
                if p:
                    piece = {'locale': p['id'], 'nom': p['nom'], 'taille': p['taille'], 'sha256': p['sha256']}
            charge = {'t': 'message', 'gid': gid, 'fil': fil, 'auteur_nom': m['author_name'], 'auteur_role': m['author_role'],
                      'genre': m['kind'], 'texte': m['text'], 'quand': m['at'], 'piece': piece}
            desk.db.execute('INSERT INTO liaison56103_messages(gid,message,thread,origine,recu_le) VALUES(?,?,?,?,?)',
                            (gid, m['id'], row['id'], 'local', _maintenant()))
            for cible in cibles:
                desk.db.execute('INSERT OR IGNORE INTO liaison56103_sortie(poste,gid,genre,thread,charge,cree_le) VALUES(?,?,?,?,?,?)',
                                (cible, gid, 'message', row['id'], json.dumps(charge, ensure_ascii=False), _maintenant()))
            total += 1
        desk.db.execute('UPDATE liaison56103_fils SET exporte_jusqu_a=? WHERE thread=?', (nouveaux[-1]['id'], row['id']))
    desk.db.commit()
    return total


# ------------------------------------------------------------------------------------------------ messages : arrivée
def _dossier_pieces(desk):
    d = Path(desk.c['state_dir']) / 'liaison56103' / 'pieces'
    d.mkdir(parents=True, exist_ok=True, mode=0o700)
    return d


def nettoyer_nom_fichier(nom):
    nom = re.sub(r'[\x00-\x1f\x7f/\\:*?"<>|]', '_', str(nom or '')).strip().strip('.')
    return (nom or 'piece-jointe')[:120]


def _ranger_piece(desk, donnees, sha):
    chemin = _dossier_pieces(desk) / sha
    if not chemin.is_file():
        tmp = chemin.with_name(sha + '.' + secrets.token_hex(4) + '.tmp')
        fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'wb') as f:
            f.write(donnees)
        os.replace(tmp, chemin)
    return chemin


def chemin_piece(desk, sha):
    if not re.fullmatch(r'[0-9a-f]{64}', str(sha or '')):
        raise Stop('piece_absente')
    return Path(desk.c['state_dir']) / 'liaison56103' / 'pieces' / sha


def _fil_local(desk, fil, expediteur, moi):
    """Fil local d'un fil global reçu : retrouvé par son identifiant, ou créé (participants : le compte du poste + les autres postes)."""
    mx = _mx()
    lien = desk.db.execute('SELECT * FROM liaison56103_fils WHERE gid=?', (fil['gid'],)).fetchone()
    compte = compte_du_poste(desk)
    membres = [m['id'] for m in fil.get('membres') or [] if isinstance(m, dict) and ID_RE.fullmatch(str(m.get('id') or ''))][:mx.MAX_GROUP]
    if moi not in membres or expediteur not in membres:
        raise Stop('fil_non_membre')
    if lien:
        tid = lien['thread']
        if lien['genre'] == 'groupe' and expediteur == lien['createur']:   # seul le poste créateur change les membres d'un groupe relié
            voulus = {PARTICIPANT + m for m in membres if m != moi}
            for owner in voulus:
                mx._join(desk, tid, owner, 1)
            for r in desk.db.execute("SELECT owner FROM participants5650 WHERE thread=? AND owner LIKE 'liaison:%' AND left_at=''", (tid,)).fetchall():
                if r['owner'] not in voulus:
                    desk.db.execute('UPDATE participants5650 SET left_at=? WHERE thread=? AND owner=?', (_maintenant(), tid, r['owner']))
            if fil.get('titre'):
                desk.db.execute("UPDATE fils5650 SET title=? WHERE id=? AND kind='groupe'", (re.sub(r'\s+', ' ', str(fil['titre']))[:120], tid))
        return tid
    genre = fil.get('genre')
    if genre == 'direct':
        if len(membres) != 2 or fil['gid'] != _gid_direct(moi, expediteur):
            raise Stop('fil_invalide')
        cle = '|'.join(sorted((compte, PARTICIPANT + expediteur)))
        row = desk.db.execute('SELECT id FROM fils5650 WHERE direct_key=?', (cle,)).fetchone()
        if row:
            tid = row['id']
        else:
            tid = desk.db.execute("INSERT INTO fils5650(kind,title,direct_key,created_by,created,updated) VALUES('direct','',?,?,?,?)",
                                  (cle, PARTICIPANT + expediteur, _maintenant(), _maintenant())).lastrowid
        for owner in (compte, PARTICIPANT + expediteur):
            mx._join(desk, tid, owner)
    elif genre == 'groupe':
        if not GID_RE.fullmatch(fil['gid']) or not fil['gid'].startswith('g-') or fil.get('createur') not in membres:
            raise Stop('fil_invalide')
        titre = re.sub(r'\s+', ' ', str(fil.get('titre') or '')).strip()[:120] or 'Groupe relié'
        tid = desk.db.execute("INSERT INTO fils5650(kind,title,created_by,created,updated) VALUES('groupe',?,?,?,?)",
                              (titre, PARTICIPANT + str(fil['createur']), _maintenant(), _maintenant())).lastrowid
        for owner in [compte] + [PARTICIPANT + m for m in membres if m != moi]:
            mx._join(desk, tid, owner)
        mx._pilote(desk, tid, 'groupe')   # Pilote de CE poste, pour les personnes de CE poste qui l'interpellent
    else:
        raise Stop('fil_invalide')
    desk.db.execute('INSERT OR IGNORE INTO liaison56103_fils(thread,gid,genre,createur,exporte_jusqu_a) VALUES(?,?,?,?,?)',
                    (tid, fil['gid'], genre, str(fil.get('createur') or expediteur), desk.db.execute(
                        'SELECT COALESCE(MAX(id),0) FROM messages5650 WHERE thread=?', (tid,)).fetchone()[0]))
    return tid


def recevoir_message(desk, expediteur, env, donnees=b'', moi=None):
    """Message d'un poste appairé. Jamais d'appel à Pilote ici : seul le poste qui l'interpelle fait tourner l'IA. Doublon : accusé."""
    mx = _mx()
    mx.ensure_schema(desk)
    gid = str(env.get('gid') or '')
    if not GID_RE.fullmatch(gid) or not gid.startswith('m-'):
        raise Stop('message_invalide')
    if desk.db.execute('SELECT 1 FROM liaison56103_messages WHERE gid=?', (gid,)).fetchone():
        return {'doublon': True}
    fil = env.get('fil') if isinstance(env.get('fil'), dict) else {}
    if not GID_RE.fullmatch(str(fil.get('gid') or '')):
        raise Stop('fil_invalide')
    texte = str(env.get('texte') or '').replace('\r\n', '\n').strip()[:mx.MAX_TEXT]
    if not texte:
        raise Stop('message_vide')
    piece = env.get('piece') if isinstance(env.get('piece'), dict) else None
    sha = ''
    if piece:
        try:
            taille = int(piece.get('taille'))
        except (TypeError, ValueError):
            raise Stop('piece_invalide') from None
        if taille > MAX_PIECE or len(donnees) > MAX_PIECE:
            raise Stop('piece_trop_grande')
        sha = hashlib.sha256(donnees).hexdigest()
        if taille != len(donnees) or not hmac.compare_digest(sha, str(piece.get('sha256') or '')):
            raise Stop('empreinte_piece_invalide')
    elif donnees:
        raise Stop('message_invalide')
    moi = moi or identite(desk.c['state_dir'], creer=False).id
    poste = desk.db.execute('SELECT nom FROM liaison56103_postes WHERE id=?', (expediteur,)).fetchone()
    nom_poste = (poste['nom'] if poste else '') or 'Poste relié'
    try:
        tid = _fil_local(desk, fil, expediteur, moi)
        ia = env.get('genre') == 'ia'
        auteur_nom = ('Pilote (poste de %s)' % nom_poste) if ia else nettoyer_nom(env.get('auteur_nom'), nom_poste)
        lien = libelle = ''
        if piece:
            chemin = _ranger_piece(desk, donnees, sha)
            pid = desk.db.execute('INSERT INTO liaison56103_pieces(message,thread,nom,taille,sha256,cree_le) VALUES(0,?,?,?,?,?)',
                                  (tid, nettoyer_nom_fichier(piece.get('nom')), len(donnees), sha, _maintenant())).lastrowid
            lien, libelle = '/api440/m56103/liaison/piece?id=%d' % pid, ('Télécharger ' + nettoyer_nom_fichier(piece.get('nom')))[:120]
            del chemin
        compte = compte_du_poste(desk)
        membres = augmenter(desk, {})
        membres[compte] = {'owner': compte, 'name': nom_du_poste(desk)}
        mentions = mx._mentions(texte, membres)
        mid = desk.db.execute('INSERT INTO messages5650(thread,author,author_name,author_role,kind,text,link,link_label,mentions,at) '
                              'VALUES(?,?,?,?,?,?,?,?,?,?)', (tid, PARTICIPANT + expediteur, auteur_nom[:120], 'ia' if ia else 'poste relié',
                                                             'ia' if ia else 'message', texte, lien, libelle, json.dumps(mentions), _maintenant())).lastrowid
        if piece:
            desk.db.execute('UPDATE liaison56103_pieces SET message=? WHERE id=?', (mid, pid))
        desk.db.execute('INSERT INTO liaison56103_messages(gid,message,thread,origine,recu_le) VALUES(?,?,?,?,?)', (gid, mid, tid, expediteur, _maintenant()))
        desk.db.execute('UPDATE liaison56103_fils SET exporte_jusqu_a=MAX(exporte_jusqu_a,?) WHERE thread=?', (mid, tid))
        desk.db.execute('UPDATE fils5650 SET updated=?,last_message=? WHERE id=?', (_maintenant(), mid, tid))
        desk.db.commit()
    except Exception:
        if desk.db.in_transaction:
            desk.db.rollback()
        raise
    return {'message': mid, 'fil': tid}


# ------------------------------------------------------------------------------------------------ pièces jointes (dépôt local)
def deposer_piece(desk, me, nom, role, tid, donnees, nom_fichier, members, hidden=frozenset()):
    """Pièce jointe d'un fil relié : 25 Mo au plus, rangée par empreinte SHA-256 (fichier 0600), puis message avec lien de téléchargement."""
    mx = _mx()
    row = mx._check(desk, tid, me, hidden)
    if not any(o.startswith(PARTICIPANT) for o in _ids_participants(desk, row['id'])):
        raise Stop('fil_non_relie')
    donnees = bytes(donnees or b'')
    if not donnees:
        raise Stop('piece_vide')
    if len(donnees) > MAX_PIECE:
        raise Stop('piece_trop_grande')
    sha = hashlib.sha256(donnees).hexdigest()
    _ranger_piece(desk, donnees, sha)
    nom_fichier = nettoyer_nom_fichier(nom_fichier)
    pid = desk.db.execute('INSERT INTO liaison56103_pieces(message,thread,nom,taille,sha256,cree_le) VALUES(0,?,?,?,?,?)',
                          (row['id'], nom_fichier, len(donnees), sha, _maintenant())).lastrowid
    desk.db.commit()
    out = mx.post(desk, me, nom, role, row['id'], 'Pièce jointe : %s (%s)' % (nom_fichier, taille_lisible(len(donnees))), members, hidden,
                  '/api440/m56103/liaison/piece?id=%d' % pid, ('Télécharger ' + nom_fichier)[:120])
    desk.db.execute('UPDATE liaison56103_pieces SET message=? WHERE id=?', (out['id'], pid))
    desk.db.commit()
    return {'piece': pid, 'message': out['id'], 'sha256': sha, 'taille': len(donnees)}


def lire_piece(desk, me, pid, hidden=frozenset()):
    """Pièce d'un fil que la personne lit ; absente ou interdite : même refus neutre."""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        raise Stop('piece_absente') from None
    p = desk.db.execute('SELECT * FROM liaison56103_pieces WHERE id=?', (pid,)).fetchone()
    if not p:
        raise Stop('piece_absente')
    try:
        _mx()._check(desk, p['thread'], me, hidden)
    except Stop:
        raise Stop('piece_absente') from None
    chemin = chemin_piece(desk, p['sha256'])
    try:
        donnees = chemin.read_bytes()
    except OSError:
        raise Stop('piece_absente') from None
    if hashlib.sha256(donnees).hexdigest() != p['sha256']:
        raise Stop('piece_alteree')
    return p['nom'], donnees


def taille_lisible(n):
    if n < 1024:
        return '%d o' % n
    if n < 1024 * 1024:
        return '%.0f Ko' % (n / 1024)
    return '%.1f Mo' % (n / 1024 / 1024)


def etat_fil(desk, me, tid, hidden=frozenset()):
    """Lecture seule : le fil est-il relié ; présence des postes ; messages en attente de remise (le poste éteint est nommé)."""
    row = _mx()._check(desk, tid, me, hidden)
    relies = [o[len(PARTICIPANT):] for o in _ids_participants(desk, row['id']) if o.startswith(PARTICIPANT)]
    if not relies:
        return {'lie': False}
    out = []
    for pid in relies:
        p = desk.db.execute('SELECT * FROM liaison56103_postes WHERE id=?', (pid,)).fetchone()
        attente = desk.db.execute("SELECT COUNT(*) FROM liaison56103_sortie WHERE poste=? AND thread=? AND etat='en_attente'", (pid, row['id'])).fetchone()[0]
        refus = desk.db.execute("SELECT motif FROM liaison56103_sortie WHERE poste=? AND thread=? AND etat='refuse' ORDER BY id DESC LIMIT 1",
                                (pid, row['id'])).fetchone()
        if not p:
            out.append({'id': pid, 'nom': 'Poste inconnu', 'presence': 'hors_ligne', 'vu_le': '', 'attente': attente, 'appaire': False})
            continue
        p = dict(p)
        out.append({'id': pid, 'nom': p['nom'] or 'Poste relié', 'presence': presence_de(p), 'vu_le': p['contact_le'], 'attente': attente,
                    'appaire': not p['retire'] and p['cle_signature'] is not None, 'refus': refus['motif'] if refus else ''})
    return {'lie': True, 'postes': out, 'attente': sum(p['attente'] for p in out), 'actif': est_active(desk)}


# ------------------------------------------------------------------------------------------------ réglages partagés
GENRES = {'fournisseur_ia': 'Fournisseur d’IA (clé d’API)', 'connecteur_mcp': 'Connecteur MCP', 'connexion_api': 'Connexion API du cabinet'}
PERSONNEL = re.compile(r'imap|smtp|oauth|refresh|access_token|jeton_oauth|session|cookie|password|mot_?de_?passe|username|chatgpt|siwc|gmail', re.I)
TYPES_PERSONNELS = ('chatgpt', 'ollama')
CHAMPS_IA = ('type', 'url', 'model', 'enabled', 'external_data_allowed', 'zdr_required', 'monthly_budget_usd', 'per_request_budget_usd',
             'input_usd_per_million', 'output_usd_per_million')
CHAMPS_MCP = ('source_url', 'name', 'endpoint', 'auth_type', 'external_data_allowed', 'purposes', 'license')


def personnel(*noms):
    """Vrai si un nom de réglage ou de champ désigne un identifiant personnel (jamais partagé)."""
    return any(PERSONNEL.search(str(n or '')) for n in noms)


def _lire_secret(chemin):
    from .common import read_secret
    if not chemin or not Path(chemin).is_file():
        return ''
    return read_secret(str(chemin))


def catalogue(desk):
    """Réglages diffusables de ce poste (sans aucune valeur secrète) : genre, clé, libellé, champs secrets, partageable ou motif."""
    out = []
    for r in desk.db.execute("SELECT key, value FROM settings WHERE key LIKE 'ai:provider:%' ORDER BY key"):
        try:
            v = json.loads(r['value'])
        except (TypeError, ValueError):
            continue
        if not isinstance(v, dict):
            continue
        pid = r['key'].split(':', 2)[-1]
        perso = v.get('type') in TYPES_PERSONNELS
        out.append({'genre': 'fournisseur_ia', 'cle': pid, 'libelle': '%s — %s %s' % (pid, v.get('type', ''), v.get('model', '')),
                    'secrets': ['api_key'] if v.get('secret_file') else [], 'partageable': not perso,
                    'motif': 'abonnement personnel ou moteur local : jamais partagé' if perso else ''})
    for r in desk.db.execute("SELECT key, value FROM settings WHERE key LIKE 'lawve:item:%' ORDER BY key"):
        try:
            v = json.loads(r['value'])
        except (TypeError, ValueError):
            continue
        if not isinstance(v, dict) or v.get('kind') != 'connector':
            continue
        perso = v.get('auth_type') == 'oauth2'
        out.append({'genre': 'connecteur_mcp', 'cle': str(v.get('id') or ''), 'libelle': str(v.get('name') or v.get('id') or ''),
                    'secrets': ['token'] if v.get('auth_type') == 'bearer' else [], 'partageable': not perso,
                    'motif': 'jeton OAuth personnel : jamais partagé' if perso else ''})
    from .affichage5696 import table_presente
    if table_presente(desk.db, 'connexions5652'):
        from .connexions5652 import PROVIDERS
        for r in desk.db.execute('SELECT * FROM connexions5652 ORDER BY provider, environment'):
            spec = PROVIDERS.get(r['provider']) or {}
            cabinet = r['owner'] == 'cabinet'
            secrets_ = [f['name'] for f in spec.get('fields', []) if f['secret'] and not personnel(f['name'])]
            out.append({'genre': 'connexion_api', 'cle': r['id'], 'libelle': '%s (%s)' % (r['label'] or spec.get('label', r['provider']), r['environment']),
                        'secrets': secrets_, 'partageable': cabinet,
                        'motif': '' if cabinet else 'connexion personnelle d’un compte : jamais partagée'})
    return out


def _element_catalogue(desk, genre, cle):
    """Valeurs (non secrètes) et secrets en clair (mémoire seulement) d'un réglage diffusable ; refus des identifiants personnels."""
    if genre not in GENRES:
        raise Stop('reglage_inconnu')
    if personnel(cle):
        raise Stop('reglage_personnel_non_partageable')
    if genre == 'fournisseur_ia':
        v = desk.settings('ai:provider:' + str(cle), None)
        if not isinstance(v, dict):
            raise Stop('reglage_inconnu')
        if v.get('type') in TYPES_PERSONNELS:
            raise Stop('reglage_personnel_non_partageable')
        valeurs = {k: v.get(k) for k in CHAMPS_IA if k in v}
        clair = {'api_key': _lire_secret(v.get('secret_file'))} if v.get('secret_file') else {}
        libelle = '%s — %s %s' % (cle, v.get('type', ''), v.get('model', ''))
    elif genre == 'connecteur_mcp':
        v = desk.settings('lawve:item:' + str(cle), None)
        if not isinstance(v, dict) or v.get('kind') != 'connector':
            raise Stop('reglage_inconnu')
        if v.get('auth_type') == 'oauth2':
            raise Stop('reglage_personnel_non_partageable')
        valeurs = {k: v.get(k) for k in CHAMPS_MCP if k in v}
        clair = {'token': _lire_secret(v.get('secret_file'))} if v.get('auth_type') == 'bearer' else {}
        libelle = str(v.get('name') or cle)
    else:
        from .connexions5652 import PROVIDERS, _vault
        r = desk.db.execute('SELECT * FROM connexions5652 WHERE id=?', (str(cle),)).fetchone()
        if not r:
            raise Stop('reglage_inconnu')
        if r['owner'] != 'cabinet':
            raise Stop('reglage_personnel_non_partageable')
        spec = PROVIDERS.get(r['provider']) or {}
        params = json.loads(r['params_json'] or '{}')
        valeurs = {'provider': r['provider'], 'environment': r['environment'], 'label': r['label']}
        for f in spec.get('fields', []):
            if not f['secret'] and not personnel(f['name']) and params.get(f['name']):
                valeurs[f['name']] = params[f['name']]
        if 'partage_piste' in params:
            valeurs['partage_piste'] = bool(params['partage_piste'])
        poses = json.loads(r['secrets_json'] or '{}')
        clair = {}
        for f in spec.get('fields', []):
            if f['secret'] and not personnel(f['name']) and f['name'] in poses:
                valeur = _lire_secret(_vault(desk, r['id'], f['name']))
                if valeur:
                    clair[f['name']] = valeur
        libelle = '%s (%s)' % (r['label'] or spec.get('label', r['provider']), r['environment'])
    if personnel(*valeurs.keys()) or personnel(*clair.keys()):
        raise Stop('reglage_personnel_non_partageable')
    return valeurs, {k: v for k, v in clair.items() if v}, libelle


def _cle_secret(partage, eph_pub, dest_pub, gid, nom, dest):
    return _hkdf(partage, None, ETIQUETTE + b' secret' + eph_pub + dest_pub + gid.encode() + b'|' + nom.encode() + b'|' + dest.encode(), 32)


def chiffrer_secret(valeur, dest_id, dest_cle_echange, gid, nom):
    """Chiffrement de bout en bout pour UN poste (X25519 éphémère × statique, HKDF, ChaCha20-Poly1305)."""
    k = _crypto()
    eph = k['X'].generate()
    eph_pub = _brut_public(eph.public_key())
    cle = _cle_secret(eph.exchange(k['XPub'].from_public_bytes(bytes(dest_cle_echange))), eph_pub, bytes(dest_cle_echange), gid, nom, dest_id)
    nonce = secrets.token_bytes(12)
    chiffre = k['AEAD'](cle).encrypt(nonce, str(valeur).encode('utf-8'), (gid + '|' + nom + '|' + dest_id).encode())
    return {'eph': _b64(eph_pub), 'n': _b64(nonce), 'c': _b64(chiffre)}


def dechiffrer_secret(ident, boite, gid, nom):
    k = _crypto()
    eph_pub = _d64(boite.get('eph'), 32)
    cle = _cle_secret(ident.echanger(eph_pub), eph_pub, ident.cle_echange, gid, nom, ident.id)
    try:
        return k['AEAD'](cle).decrypt(_d64(boite.get('n'), 12), _d64(boite.get('c')), (gid + '|' + nom + '|' + ident.id).encode()).decode('utf-8')
    except k['InvalidTag']:
        raise Stop('secret_indechiffrable') from None


def diffuser(desk, genre, cle, par, role):
    """Poste administrateur : diffuse UN réglage aux postes appairés ; secrets chiffrés pour chacun, jamais écrits en clair."""
    if role != 'administrateur':
        raise Stop('role_insuffisant')
    if not est_active(desk):
        raise Stop('liaison_desactivee')
    valeurs, clair, libelle = _element_catalogue(desk, genre, cle)
    gid = 'r-' + secrets.token_hex(16)
    cibles = postes(desk)
    etats = {}
    for p in cibles:
        if not p['cle_echange']:
            etats[p['id']] = 'sans_cle'
            continue
        boites = {nom: chiffrer_secret(valeur, p['id'], p['cle_echange'], gid, nom) for nom, valeur in clair.items()}
        charge = {'t': 'reglage', 'gid': gid, 'genre': genre, 'cle': str(cle), 'libelle': libelle, 'valeurs': valeurs, 'secrets': boites,
                  'role': 'administrateur', 'par': str(par)[:80]}
        desk.db.execute('INSERT INTO liaison56103_sortie(poste,gid,genre,thread,charge,cree_le) VALUES(?,?,?,0,?,?)',
                        (p['id'], gid, 'reglage', json.dumps(charge, ensure_ascii=False), _maintenant()))
        etats[p['id']] = 'en_attente'
    del clair
    desk.db.execute('INSERT INTO liaison56103_diffusions(gid,genre,cle,libelle,etats,cree_le,par) VALUES(?,?,?,?,?,?,?)',
                    (gid, genre, str(cle), libelle, json.dumps(etats), _maintenant(), str(par)[:80]))
    desk.db.commit()
    desk.audit('liaison56103_reglage_diffuse', {'genre': genre, 'cle': str(cle), 'postes': len([e for e in etats.values() if e == 'en_attente'])})
    return {'gid': gid, 'postes': etats, 'libelle': libelle}


def recevoir_reglage(desk, expediteur, env):
    """Réglage proposé par un poste appairé : mis « en attente de décision » (Appliquer ou Refuser), secrets toujours chiffrés."""
    gid = str(env.get('gid') or '')
    if not GID_RE.fullmatch(gid) or not gid.startswith('r-'):
        raise Stop('reglage_invalide')
    if desk.db.execute('SELECT 1 FROM liaison56103_reglages_recus WHERE gid=?', (gid,)).fetchone():
        return {'doublon': True}
    genre, cle = str(env.get('genre') or ''), str(env.get('cle') or '')[:120]
    valeurs = env.get('valeurs') if isinstance(env.get('valeurs'), dict) else {}
    boites = env.get('secrets') if isinstance(env.get('secrets'), dict) else {}
    if genre not in GENRES or not cle:
        raise Stop('reglage_invalide')
    if env.get('role') != 'administrateur':
        raise Stop('diffusion_reservee_administrateur')
    if personnel(cle, *valeurs.keys(), *boites.keys()) or valeurs.get('type') in TYPES_PERSONNELS or valeurs.get('auth_type') == 'oauth2':
        raise Stop('identifiant_personnel_refuse')
    if len(json.dumps(valeurs)) > 20000 or len(boites) > 10:
        raise Stop('reglage_invalide')
    desk.db.execute('INSERT INTO liaison56103_reglages_recus(gid,poste,genre,cle,libelle,valeurs,secrets_chiffres,recu_le) VALUES(?,?,?,?,?,?,?,?)',
                    (gid, expediteur, genre, cle, nettoyer_nom(env.get('libelle'), cle)[:160], json.dumps(valeurs, ensure_ascii=False),
                     json.dumps(boites), _maintenant()))
    desk.db.commit()
    desk.audit('liaison56103_reglage_recu', {'genre': genre, 'cle': cle, 'poste': expediteur})
    return {'en_attente': True}


def _appliquer(desk, genre, cle, valeurs, clair, compte):
    if genre == 'fournisseur_ia':
        from .ai_gateway import save_provider
        return save_provider(desk, cle, valeurs.get('type'), valeurs.get('url'), valeurs.get('model'), api_key=clair.get('api_key', ''),
                             enabled=bool(valeurs.get('enabled')), allow_external=bool(valeurs.get('external_data_allowed')),
                             zdr_required=bool(valeurs.get('zdr_required')), monthly_budget_usd=valeurs.get('monthly_budget_usd') or 0,
                             per_request_budget_usd=valeurs.get('per_request_budget_usd') or 0,
                             input_usd_per_million=valeurs.get('input_usd_per_million') or 0,
                             output_usd_per_million=valeurs.get('output_usd_per_million') or 0)
    if genre == 'connecteur_mcp':
        from .extensions364 import register_item
        return register_item(desk, valeurs.get('source_url'), valeurs.get('name') or '', valeurs.get('endpoint') or '', valeurs.get('auth_type') or 'none',
                             clair.get('token', ''), bool(valeurs.get('external_data_allowed')), valeurs.get('purposes') or [], valeurs.get('license') or '')
    from .connexions5652 import save
    data = {k: v for k, v in valeurs.items() if not personnel(k)}
    data.update({k: v for k, v in clair.items() if not personnel(k)})
    data['portee'] = 'cabinet'
    data.pop('id', None)
    return save(desk, compte, 'administrateur', data)


def decider_reglage(desk, rid, appliquer_, par, role):
    """Appliquer (secrets déchiffrés en mémoire puis rangés au coffre par le service du réglage) ou Refuser ; journalisé dans les deux cas."""
    if role != 'administrateur':
        raise Stop('role_insuffisant')
    try:
        rid = int(rid)
    except (TypeError, ValueError):
        raise Stop('reglage_absent') from None
    r = desk.db.execute('SELECT * FROM liaison56103_reglages_recus WHERE id=?', (rid,)).fetchone()
    if not r:
        raise Stop('reglage_absent')
    if r['etat'] != 'en_attente':
        raise Stop('reglage_deja_decide')
    valeurs = json.loads(r['valeurs'] or '{}')
    boites = json.loads(r['secrets_chiffres'] or '{}')
    etat, motif = 'refuse', ''
    if appliquer_:
        try:
            if personnel(r['cle'], *valeurs.keys(), *boites.keys()):
                raise Stop('identifiant_personnel_refuse')
            ident = identite(desk.c['state_dir'], creer=False)
            clair = {nom: dechiffrer_secret(ident, b, r['gid'], nom) for nom, b in boites.items()}
            _appliquer(desk, r['genre'], r['cle'], valeurs, clair, compte_du_poste(desk))
            del clair
            etat = 'applique'
        except Stop as ex:
            if desk.db.in_transaction:
                desk.db.rollback()
            etat, motif = 'echec', str(ex).split(':')[0]
    cur = desk.db.execute("UPDATE liaison56103_reglages_recus SET etat=?,motif=?,decide_le=?,decide_par=?,secrets_chiffres='{}' WHERE id=? AND etat='en_attente'",
                          (etat, motif, _maintenant(), str(par)[:80], rid))
    if not cur.rowcount:
        desk.db.rollback()
        raise Stop('reglage_deja_decide')
    reponse = {'t': 'etat_reglage', 'gid': r['gid'], 'etat': etat, 'motif': motif}
    desk.db.execute('INSERT OR IGNORE INTO liaison56103_sortie(poste,gid,genre,thread,charge,cree_le) VALUES(?,?,?,0,?,?)',
                    (r['poste'], 'r-' + secrets.token_hex(16), 'etat_reglage', json.dumps(reponse), _maintenant()))
    desk.db.commit()
    desk.audit({'applique': 'liaison56103_reglage_applique', 'refuse': 'liaison56103_reglage_refuse'}.get(etat, 'liaison56103_reglage_echec'),
               {'genre': r['genre'], 'cle': r['cle'], 'poste': r['poste'], 'secrets': sorted(boites), 'motif': motif})
    return {'etat': etat, 'motif': motif, 'libelle': r['libelle']}


def recevoir_etat_reglage(desk, expediteur, env):
    gid = str(env.get('gid') or '')
    etat = str(env.get('etat') or '')
    if etat not in ('applique', 'refuse', 'echec'):
        raise Stop('reglage_invalide')
    r = desk.db.execute('SELECT etats FROM liaison56103_diffusions WHERE gid=?', (gid,)).fetchone()
    if r:
        etats = json.loads(r['etats'] or '{}')
        if expediteur in etats:
            etats[expediteur] = etat
            desk.db.execute('UPDATE liaison56103_diffusions SET etats=? WHERE gid=?', (json.dumps(etats), gid))
            desk.db.commit()
    return {}


# ------------------------------------------------------------------------------------------------ service : écoute, découverte, remise
def _noter_contact(desk, poste_id, adresse=None, port=None, presence=None, nom=None):
    champs, valeurs = ['contact_le=?', "erreur=''", 'echecs=0', 'prochain_essai=0'], [_maintenant()]
    if adresse:
        champs.append('adresse=?')
        valeurs.append(adresse)
    if port:
        try:
            valeurs.append(verifier_port(port))
            champs.append('port=?')
        except Stop:
            pass
    if presence in ('en_ligne', 'absent'):
        champs += ['presence=?', 'presence_le=?']
        valeurs += [presence, _maintenant()]
    if nom:
        champs.append('nom=?')
        valeurs.append(nettoyer_nom(nom))
    desk.db.execute('UPDATE liaison56103_postes SET %s WHERE id=? AND retire=0' % ','.join(champs), valeurs + [poste_id])
    desk.db.commit()


def _traiter(desk, poste_id, obj, donnees, moi):
    t = obj.get('t')
    if t == 'presence':
        _noter_contact(desk, poste_id, presence=obj.get('etat'), nom=obj.get('nom'))
        return {'t': 'ok'}
    if t == 'message':
        return {'t': 'ok', **recevoir_message(desk, poste_id, obj, donnees, moi)}
    if t == 'reglage':
        return {'t': 'ok', **recevoir_reglage(desk, poste_id, obj)}
    if t == 'etat_reglage':
        return {'t': 'ok', **recevoir_etat_reglage(desk, poste_id, obj)}
    raise Stop('trame_inconnue')


class Liaison:
    """Écoute TCP (une adresse privée), découverte UDP, remise de la file d'envoi. ``ouvrir`` rend un ``Desk`` neuf (un par fil d'exécution).
    ``boucle`` : 127.0.0.1 accepté (essais dans un seul processus, jamais depuis l'interface)."""

    def __init__(self, ouvrir, boucle=False):
        self.ouvrir, self.boucle = ouvrir, boucle
        self.serveur = self.udp = None
        self.adresse, self.port, self.port_udp = '', 0, 0
        self.arret = threading.Event()
        self.fils = []
        self.places = threading.BoundedSemaphore(MAX_SESSIONS)
        self.ident = None
        self.nom = ''

    # -------------------------------------------------------------- démarrage
    def demarrer(self, adresse, port=PORT_TCP, decouverte=False, port_udp=PORT_UDP):
        adresse = verifier_adresse(adresse, self.boucle)
        desk = self.ouvrir()
        try:
            self.ident = identite(desk.c['state_dir'])
            self.nom = nom_du_poste(desk)
        finally:
            desk.db.close()
        famille = socket.AF_INET6 if ':' in adresse else socket.AF_INET
        s = socket.socket(famille, socket.SOCK_STREAM)
        if os.name == 'nt' and hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
            s.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        elif os.name != 'nt':
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((adresse, int(port)))
            s.listen(16)
        except OSError:
            s.close()
            raise Stop('port_liaison_occupe') from None
        s.settimeout(0.5)
        self.serveur, self.adresse, self.port = s, adresse, s.getsockname()[1]
        self._lancer(self._accepter, 'axiorhub-liaison-ecoute')
        if decouverte:
            self._demarrer_udp(port_udp)
        return self

    def _lancer(self, cible, nom):
        t = threading.Thread(target=cible, name=nom, daemon=True)
        t.start()
        self.fils.append(t)

    def _demarrer_udp(self, port_udp, adresse_udp=''):
        u = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        if os.name != 'nt':
            u.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        u.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        try:
            u.bind((adresse_udp, int(port_udp)))
        except OSError:
            u.close()
            return False
        u.settimeout(0.5)
        self.udp, self.port_udp = u, u.getsockname()[1]
        self._lancer(self._ecouter_udp, 'axiorhub-liaison-decouverte')
        return True

    def arreter(self):
        self.arret.set()
        for s in (self.serveur, self.udp):
            if s is not None:
                try:
                    s.close()
                except OSError:
                    pass
        for t in self.fils:
            t.join(3)
        self.serveur = self.udp = None

    # -------------------------------------------------------------- écoute
    def _accepter(self):
        while not self.arret.is_set():
            try:
                conn, origine = self.serveur.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            if not self.places.acquire(blocking=False):
                conn.close()
                continue
            threading.Thread(target=self._session, args=(conn, origine), name='axiorhub-liaison-session', daemon=True).start()

    def _session(self, conn, origine):
        desk = None
        try:
            conn.settimeout(DELAI_SOCKET)
            try:
                adresse = verifier_adresse(origine[0].split('%')[0], self.boucle)
            except Stop:
                return                                    # jamais de réponse hors du réseau local
            t1 = _lire_brut(conn, MAX_CLAIR)
            h1 = _lire_json(t1)
            if h1.get('v') != VERSION:
                _envoyer_brut(conn, _json({'t': 'refus', 'motif': 'version_incompatible'}))
                return
            desk = self.ouvrir()
            if not est_active(desk):
                return
            if h1.get('mode') == 'appairage':
                _serveur_appairage(conn, self.ident, desk, h1, adresse, nom_du_poste(desk), self.port)
                return
            canal = ouvrir_serveur(conn, self.ident, desk, t1, h1, nom_du_poste(desk), self.port)
            poste_id = canal.poste['id']
            _noter_contact(desk, poste_id, adresse, h1.get('port'), nom=h1.get('nom'))
            conn.settimeout(120)
            while not self.arret.is_set():
                obj, donnees = canal.recevoir()
                if obj.get('t') == 'fin':
                    canal.envoyer({'t': 'presence', 'etat': presence_locale(desk), 'nom': nom_du_poste(desk)})
                    return
                try:
                    reponse = _traiter(desk, poste_id, obj, donnees, self.ident.id)
                except Stop as ex:
                    reponse = {'t': 'refus', 'motif': str(ex).split(':')[0][:60]}
                canal.envoyer({**reponse, 'gid': obj.get('gid', '')})
        except (Stop, OSError, ValueError):
            return
        finally:
            if desk is not None:
                try:
                    desk.db.close()
                except Exception:   # noqa: BLE001
                    pass
            try:
                conn.close()
            except OSError:
                pass
            self.places.release()

    # -------------------------------------------------------------- découverte
    def annonce(self):
        return _json({'axh': 'liaison56103', 'v': VERSION, 'id': self.ident.id, 'nom': self.nom, 'port': self.port})

    def annoncer(self, cible=('255.255.255.255', PORT_UDP)):
        if self.udp is None:
            return False
        try:
            self.udp.sendto(self.annonce(), cible)
            return True
        except OSError:
            return False

    def _ecouter_udp(self):
        while not self.arret.is_set():
            try:
                data, origine = self.udp.recvfrom(MAX_ANNONCE + 1)
            except socket.timeout:
                continue
            except OSError:
                return
            desk = None
            try:
                desk = self.ouvrir()
                recevoir_annonce(desk, data, origine[0], self.ident.id, self.boucle)
            except Exception:   # noqa: BLE001 — une annonce malformée n'arrête pas la découverte
                pass
            finally:
                if desk is not None:
                    desk.db.close()

    # -------------------------------------------------------------- remise
    def synchroniser(self, forcer=False):
        """Un passage : sortie des nouveaux messages, puis contact des postes dus (file non vide ou battement de présence)."""
        desk = self.ouvrir()
        bilan = {}
        try:
            if not est_active(desk):
                return bilan
            self.nom = nom_du_poste(desk)
            exporter(desk, self.ident)
            maintenant = time.time()
            for p in postes(desk):
                if not p['adresse'] or (p['prochain_essai'] and p['prochain_essai'] > maintenant and not forcer):
                    continue
                attente = desk.db.execute("SELECT COUNT(*) FROM liaison56103_sortie WHERE poste=? AND etat='en_attente'", (p['id'],)).fetchone()[0]
                if not attente and not forcer and presence_de(p) != 'hors_ligne' and _age(p['contact_le']) < BATTEMENT:
                    continue
                from .pause5629 import checkpoint
                checkpoint(desk)
                try:
                    bilan[p['id']] = self._contacter(desk, p)
                except (Stop, OSError, ValueError) as ex:
                    code = str(ex).split(':')[0] if isinstance(ex, Stop) else 'poste_injoignable'
                    echecs = int(p['echecs'] or 0) + 1
                    desk.db.execute('UPDATE liaison56103_postes SET erreur=?,echecs=?,prochain_essai=? WHERE id=?',
                                    (code, echecs, maintenant + min(120, 5 * 2 ** min(echecs, 5)), p['id']))
                    desk.db.commit()
                    bilan[p['id']] = {'erreur': code}
        finally:
            desk.db.close()
        return bilan

    def _contacter(self, desk, p):
        adresse = verifier_adresse(p['adresse'], self.boucle)
        try:
            sock = socket.create_connection((adresse, int(p['port'] or PORT_TCP)), timeout=5)
        except OSError:
            raise Stop('poste_injoignable') from None
        remis = refuses = 0
        try:
            sock.settimeout(DELAI_SOCKET)
            canal = ouvrir_client(sock, self.ident, p, self.nom, self.port)
            _noter_contact(desk, p['id'], nom=canal.distant.get('nom'))
            sock.settimeout(120)
            canal.envoyer({'t': 'presence', 'etat': presence_locale(desk), 'nom': self.nom})
            r, _ = canal.recevoir()
            for item in desk.db.execute("SELECT * FROM liaison56103_sortie WHERE poste=? AND etat='en_attente' ORDER BY id LIMIT 200", (p['id'],)).fetchall():
                charge = json.loads(item['charge'])
                donnees = b''
                piece = charge.get('piece')
                if piece:
                    row = desk.db.execute('SELECT * FROM liaison56103_pieces WHERE id=?', (piece.get('locale'),)).fetchone()
                    try:
                        donnees = chemin_piece(desk, row['sha256']).read_bytes() if row else b''
                    except OSError:
                        donnees = b''
                    charge['piece'] = {k: piece[k] for k in ('nom', 'taille', 'sha256')}
                canal.envoyer(charge, donnees)
                r, _ = canal.recevoir()
                if r.get('t') == 'ok':
                    desk.db.execute("UPDATE liaison56103_sortie SET etat='remis',remis_le=?,tentatives=tentatives+1 WHERE id=?", (_maintenant(), item['id']))
                    remis += 1
                else:
                    desk.db.execute("UPDATE liaison56103_sortie SET etat='refuse',motif=?,tentatives=tentatives+1 WHERE id=?",
                                    (str(r.get('motif') or 'refuse')[:60], item['id']))
                    refuses += 1
                desk.db.commit()
            canal.envoyer({'t': 'fin'})
            r, _ = canal.recevoir()
            if r.get('t') == 'presence':
                _noter_contact(desk, p['id'], presence=r.get('etat'), nom=r.get('nom'))
        finally:
            try:
                sock.close()
            except OSError:
                pass
        return {'remis': remis, 'refuses': refuses}


def _age(iso):
    try:
        return (datetime.now(timezone.utc) - datetime.fromisoformat(iso)).total_seconds()
    except (TypeError, ValueError):
        return 1e9


def recevoir_annonce(desk, data, source, moi, boucle=False):
    """Annonce de découverte : source privée, format strict, aucune donnée du cabinet ; met à jour l'adresse d'un poste appairé."""
    if len(data) > MAX_ANNONCE:
        return None
    try:
        adresse = verifier_adresse(str(source).split('%')[0], boucle)
    except Stop:
        return None
    try:
        a = _lire_json(data)
    except Stop:
        return None
    if a.get('axh') != 'liaison56103' or a.get('v') != VERSION or not ID_RE.fullmatch(str(a.get('id') or '')) or a['id'] == moi:
        return None
    try:
        port = verifier_port(a.get('port'))
    except Stop:
        return None
    nom = nettoyer_nom(a.get('nom'))
    desk.db.execute('INSERT INTO liaison56103_decouverts(id,nom,adresse,port,vu_le) VALUES(?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET '
                    'nom=excluded.nom,adresse=excluded.adresse,port=excluded.port,vu_le=excluded.vu_le', (a['id'], nom, adresse, port, _maintenant()))
    desk.db.execute('UPDATE liaison56103_postes SET adresse=?,port=? WHERE id=? AND retire=0', (adresse, port, a['id']))
    desk.db.commit()
    return {'id': a['id'], 'nom': nom, 'adresse': adresse, 'port': port}


def decouverts(desk, duree=600):
    limite = (datetime.now(timezone.utc) - timedelta(seconds=duree)).isoformat(timespec='seconds')
    appaires = {r['id'] for r in desk.db.execute('SELECT id FROM liaison56103_postes WHERE retire=0 AND cle_signature IS NOT NULL')}
    return [dict(r, appaire=r['id'] in appaires) for r in desk.db.execute('SELECT * FROM liaison56103_decouverts WHERE vu_le>=? ORDER BY nom', (limite,))]


def _noter_service(desk, etat, adresse='', port=0):
    valeur = {'etat': etat, 'adresse': adresse, 'port': port}
    avant = desk.settings(REGLAGE_SERVICE, {}) or {}
    if {k: avant.get(k) for k in valeur} != valeur:
        desk.setting(REGLAGE_SERVICE, {**valeur, 'depuis': _maintenant()})


def choisir_adresse(desk):
    voulue = str(desk.settings(REGLAGE_ADRESSE, '') or '')
    locales = adresses_locales()
    if voulue and voulue in locales:
        return voulue
    return locales[0] if locales else ''


def choisir_adresse_sans_desk(c):
    from .desk import Desk
    desk = Desk(c)
    try:
        return choisir_adresse(desk)
    except Exception:   # noqa: BLE001 — en cas de doute, l'écoute en cours est gardée
        return ''
    finally:
        desk.db.close()


def service(c, intervalle=2.0, annonce=30.0):
    """Processus « liaison » du poste (``web.py liaison``), lancé par ``poste.py`` seulement quand la liaison est activée ; il s'arrête
    de lui-même dès qu'elle est désactivée (port fermé)."""
    from .desk import Desk

    def ouvrir():
        return Desk(c)
    liaison, derniere_annonce = None, 0.0
    try:
        while True:
            desk = ouvrir()
            try:
                if not est_active(desk):
                    _noter_service(desk, 'arrete')
                    return 0
                if liaison is None:
                    ensure_schema(desk)
                    adresse = choisir_adresse(desk)
                    if not adresse:
                        _noter_service(desk, 'aucune_adresse_privee')
                    else:
                        try:
                            liaison = Liaison(ouvrir).demarrer(adresse, port_ecoute(desk), decouverte=True)
                            _noter_service(desk, 'a_l_ecoute', liaison.adresse, liaison.port)
                        except Stop as ex:
                            _noter_service(desk, str(ex).split(':')[0], adresse, port_ecoute(desk))
            finally:
                desk.db.close()
            if liaison is not None:
                if time.time() - derniere_annonce > annonce:
                    if choisir_adresse_sans_desk(c) not in ('', liaison.adresse):   # adresse du poste changée (DHCP) : nouvelle écoute
                        liaison.arreter()
                        liaison = None
                        continue
                    liaison.annoncer()
                    derniere_annonce = time.time()
                try:
                    liaison.synchroniser()
                except Exception:   # noqa: BLE001 — un passage manqué est repris au suivant
                    pass
            time.sleep(intervalle if liaison is not None else 15)
    finally:
        if liaison is not None:
            liaison.arreter()
