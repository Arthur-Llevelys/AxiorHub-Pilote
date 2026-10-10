"""Coffre local chiffré (Fernet), compatible avec les secrets historiques.

La clé appartient au service et doit être sauvegardée avec le volume privé.
Ce chiffrement protège les fichiers copiés isolément ; il ne protège pas d'un
administrateur ou d'un processus disposant aussi de la clé du serveur.
"""
from contextlib import contextmanager
from .portable import fcntl   # 5.6.25 : verrous portables Linux / Windows
import os
from pathlib import Path
import tempfile

from .common import Stop

MAGIC = 'AXIORHUB-VAULT-V1:'


def _fernet(key):
    try:
        from cryptography.fernet import Fernet
        return Fernet(key)
    except (ImportError, ValueError):
        raise Stop('coffre_chiffrement_indisponible') from None


def _private_write(path, data):
    fd, temp = tempfile.mkstemp(prefix='.secret-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
        os.chmod(path, 0o600)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def write(path, value):
    path = Path(path)
    if path.is_symlink():
        raise Stop('secret_lien_symbolique_refuse')
    value = str(value or '').strip()
    if not value or len(value) > 4096 or any(x in value for x in ('\n', '\r', '\0')):
        raise Stop('secret_invalide')
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path.parent, 0o700)
    with (path.parent / '.vault.lock').open('a') as lock:
        os.chmod(path.parent / '.vault.lock', 0o600)
        fcntl.flock(lock, fcntl.LOCK_EX)
        keyfile = path.parent / '.master.key'
        if not keyfile.exists():
            try:
                from cryptography.fernet import Fernet
                _private_write(keyfile, Fernet.generate_key())
            except ImportError:
                raise Stop('coffre_chiffrement_indisponible') from None
        if keyfile.is_symlink() or keyfile.stat().st_mode & 0o077:
            raise Stop('cle_coffre_permissions_incorrectes')
        token = _fernet(keyfile.read_bytes()).encrypt(value.encode()).decode()
        _private_write(path, (MAGIC + token + '\n').encode())
    return str(path)


def decrypt(path, value):
    if not value.startswith(MAGIC):
        return value                         # import progressif, sans modifier un secret géré par root
    keyfile = Path(path).parent / '.master.key'
    if not keyfile.is_file() or keyfile.is_symlink() or keyfile.stat().st_mode & 0o077:
        raise Stop('cle_coffre_absente_ou_permissions_incorrectes')
    try:
        return _fernet(keyfile.read_bytes()).decrypt(value[len(MAGIC):].encode()).decode()
    except Stop:
        raise
    except Exception:
        raise Stop('secret_chiffre_non_dechiffrable') from None
