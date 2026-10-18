"""5.6.61 : vérification des signatures des versions (format SSHSIG d'OpenSSH, clé Ed25519), sans dépendre de ``ssh-keygen``.

Une version publiée est signée par l'auteur avec ``ssh-keygen -Y sign -n axiorhub-release``. La clé publique de confiance est livrée
avec AxiorHub (``agent/data/maj-signataires``, format « allowed_signers » d'OpenSSH) ; sur un serveur, l'administrateur peut la
remplacer dans ``/etc/axiorhub-mail-agent/maj-signataires``.

La vérification se fait en Python (bibliothèque ``cryptography``, embarquée dans l'application Windows) ; à défaut, avec
``ssh-keygen -Y verify``. Seules les clés Ed25519 sont acceptées.
"""
import base64
import hashlib
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile

NAMESPACE = 'axiorhub-release'
PRINCIPAL = 'axiorhub-pilote'
SHIPPED = Path(__file__).resolve().parent / 'data' / 'maj-signataires'
SERVER = Path('/etc/axiorhub-mail-agent/maj-signataires')


class SignatureError(Exception):
    pass


def _read_string(buf, pos):
    if pos + 4 > len(buf):
        raise SignatureError('signature_tronquee')
    (n,) = struct.unpack('>I', buf[pos:pos + 4])
    if pos + 4 + n > len(buf):
        raise SignatureError('signature_tronquee')
    return buf[pos + 4:pos + 4 + n], pos + 4 + n


def _string(data):
    return struct.pack('>I', len(data)) + data


def trusted_keys(path=None):
    """Clés Ed25519 de confiance (octets bruts de 32 octets) lues dans un fichier « allowed_signers »."""
    files = [Path(path)] if path else [p for p in (SERVER, SHIPPED) if p.is_file()]
    keys = []
    for f in files:
        try:
            lines = f.read_text(encoding='utf-8').splitlines()
        except OSError:
            continue
        for line in lines:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            parts = line.split()
            if 'ssh-ed25519' not in parts:
                continue
            i = parts.index('ssh-ed25519')
            if i + 1 >= len(parts):
                continue
            if 'namespaces=' in line and NAMESPACE not in line:
                continue
            try:
                blob = base64.b64decode(parts[i + 1])
                kind, pos = _read_string(blob, 0)
                raw, _ = _read_string(blob, pos)
            except (ValueError, SignatureError):
                continue
            if kind == b'ssh-ed25519' and len(raw) == 32:
                keys.append(raw)
        if keys:
            break                      # le fichier du serveur, s'il existe et contient une clé, prime sur la clé livrée
    return keys


def ready(path=None):
    return bool(trusted_keys(path))


def _parse(armored):
    text = armored.decode('ascii', 'replace') if isinstance(armored, bytes) else str(armored)
    body = ''.join(line.strip() for line in text.splitlines() if line.strip() and not line.startswith('-----'))
    try:
        blob = base64.b64decode(body)
    except ValueError:
        raise SignatureError('signature_illisible') from None
    if not blob.startswith(b'SSHSIG'):
        raise SignatureError('signature_illisible')
    pos = 6
    (version,) = struct.unpack('>I', blob[pos:pos + 4])
    pos += 4
    if version != 1:
        raise SignatureError('signature_version_inconnue')
    pub, pos = _read_string(blob, pos)
    namespace, pos = _read_string(blob, pos)
    reserved, pos = _read_string(blob, pos)
    hash_alg, pos = _read_string(blob, pos)
    sig, pos = _read_string(blob, pos)
    kind, p2 = _read_string(pub, 0)
    raw, _ = _read_string(pub, p2)
    sig_kind, p3 = _read_string(sig, 0)
    sig_bytes, _ = _read_string(sig, p3)
    return {'kind': kind, 'key': raw, 'namespace': namespace, 'reserved': reserved, 'hash': hash_alg, 'sig_kind': sig_kind, 'sig': sig_bytes}


def verify_bytes(message, armored, keys):
    """Vrai si ``armored`` est une signature SSHSIG valide de ``message`` par l'une des ``keys``, pour l'espace de noms des versions."""
    s = _parse(armored)
    if s['kind'] != b'ssh-ed25519' or s['sig_kind'] != b'ssh-ed25519':
        raise SignatureError('cle_non_ed25519')
    if s['namespace'] != NAMESPACE.encode():
        raise SignatureError('espace_de_noms_incorrect')
    if s['key'] not in keys:
        raise SignatureError('cle_inconnue')
    algo = {b'sha512': hashlib.sha512, b'sha256': hashlib.sha256}.get(s['hash'])
    if not algo:
        raise SignatureError('hachage_refuse')
    signed = b'SSHSIG' + _string(s['namespace']) + _string(s['reserved']) + _string(s['hash']) + _string(algo(message).digest())
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    except ImportError:
        return None                      # pas de bibliothèque : la vérification passe par ssh-keygen
    try:
        Ed25519PublicKey.from_public_bytes(s['key']).verify(s['sig'], signed)
    except InvalidSignature:
        raise SignatureError('signature_invalide') from None
    return True


def verify_file(path, sig_bytes, signers=None):
    """Vérifie un fichier téléchargé ; lève ``SignatureError`` sinon. Python d'abord, ``ssh-keygen`` en secours."""
    keys = trusted_keys(signers)
    if not keys:
        raise SignatureError('cle_de_signature_absente')
    data = Path(path).read_bytes()
    if verify_bytes(data, sig_bytes, keys):
        return True
    tool = shutil.which('ssh-keygen')
    if not tool:
        raise SignatureError('verification_impossible_sans_cryptography_ni_ssh_keygen')
    with tempfile.TemporaryDirectory() as td:
        allowed = Path(td) / 'signers'
        allowed.write_text(''.join('%s namespaces="%s" ssh-ed25519 %s\n' % (PRINCIPAL, NAMESPACE, base64.b64encode(_string(b'ssh-ed25519') + _string(k)).decode())
                                   for k in keys), encoding='utf-8')
        sig = Path(td) / 'f.sig'
        sig.write_bytes(sig_bytes)
        with open(path, 'rb') as fh:
            out = subprocess.run([tool, '-Y', 'verify', '-f', str(allowed), '-I', PRINCIPAL, '-n', NAMESPACE, '-s', str(sig)],
                                 stdin=fh, capture_output=True, timeout=60)
    if out.returncode != 0:
        raise SignatureError('signature_invalide')
    return True
