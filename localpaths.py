"""Emplacements propres au serveur, détectés au lieu d'être écrits en dur."""
import os
from pathlib import Path


def roundcube_root(web_root=Path('/var/www/html')):
    """Racine de Roundcube : AXIORHUB_ROUNDCUBE_ROOT, sinon le dossier roundcube* (version la plus récente) qui contient sa configuration."""
    forced = os.environ.get('AXIORHUB_ROUNDCUBE_ROOT', '').strip()
    if forced:
        return Path(forced)
    try:
        found = sorted((p for p in web_root.glob('roundcube*') if (p / 'config' / 'config.inc.php').is_file()), reverse=True)
    except OSError:
        found = []
    return found[0] if found else web_root / 'roundcube'
