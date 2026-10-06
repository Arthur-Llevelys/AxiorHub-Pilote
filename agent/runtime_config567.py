"""Configuration éditable par le service sans rendre /etc ni les jetons API modifiables."""
import json
import os
from pathlib import Path
import tempfile


def enable(source, state, uid, gid):
    if os.geteuid()!=0:
        raise RuntimeError('La migration de la configuration exige root.')
    source=Path(source);state=Path(state).resolve()
    target=state/'configuration567'/'config.json'
    if source.is_symlink():
        if source.resolve()!=target:
            raise RuntimeError('Lien de configuration inattendu : migration interrompue.')
        return target
    data=source.read_bytes();cfg=json.loads(data)
    if Path(cfg['state_dir']).resolve()!=state:
        raise RuntimeError('Le répertoire d’état ne correspond pas à la configuration.')
    if target.exists() or target.is_symlink():
        raise RuntimeError('Une configuration runtime existe déjà : comparer avant migration.')
    target.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
    os.chown(target.parent,uid,gid);os.chmod(target.parent,0o700)
    fd,tmp=tempfile.mkstemp(dir=target.parent,prefix='.config-')
    try:
        with os.fdopen(fd,'wb') as handle:
            handle.write(data);handle.flush();os.fsync(handle.fileno())
        os.chown(tmp,uid,gid);os.chmod(tmp,0o600);os.replace(tmp,target)
    finally:
        if os.path.exists(tmp):os.unlink(tmp)
    link=source.parent/('.config567-link-'+str(os.getpid()))
    try:
        link.symlink_to(target);os.replace(link,source)
    finally:
        link.unlink(missing_ok=True)
    return target
