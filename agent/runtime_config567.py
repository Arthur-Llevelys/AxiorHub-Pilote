"""Configuration éditable par le service sans rendre /etc ni les jetons API modifiables.

5.6.15 : si le lien /etc/axiorhub-mail-agent/config.json a été remplacé par un fichier ordinaire (commande
« axiorhub-mail mode » ou « configure » d'une version précédente), la migration rétablit le lien : la plus récente des
deux configurations est conservée, l'autre est sauvegardée, rien n'est perdu.
"""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile


def _write(target, data, uid, gid, mode=0o600):
    fd,tmp=tempfile.mkstemp(dir=target.parent,prefix='.config-')
    try:
        with os.fdopen(fd,'wb') as handle:
            handle.write(data);handle.flush();os.fsync(handle.fileno())
        os.chown(tmp,uid,gid);os.chmod(tmp,mode);os.replace(tmp,target)
    finally:
        if os.path.exists(tmp):os.unlink(tmp)


def choose(source, target):
    """Entre le fichier ordinaire de /etc et la copie runtime : (données conservées, chemin de la copie écartée ou None).

    Contenus identiques : rien à écarter. Sinon la plus récente l'emporte ; une copie runtime illisible (JSON invalide)
    est toujours écartée au profit du fichier /etc.
    """
    src=source.read_bytes();dst=target.read_bytes()
    if src==dst:return src,None
    try:json.loads(dst)
    except ValueError:return src,target
    if source.stat().st_mtime>=target.stat().st_mtime:return src,target
    return dst,source


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
    if target.is_symlink():
        raise RuntimeError('Une configuration runtime inattendue (lien) existe déjà : comparer avant migration.')
    target.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
    os.chown(target.parent,uid,gid);os.chmod(target.parent,0o700)
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    if target.exists():
        data,discarded=choose(source,target)
        if discarded is not None:
            _write(target.parent/('config-remplace-'+stamp+'.json'),discarded.read_bytes(),uid,gid)
        if Path(json.loads(data)['state_dir']).resolve()!=state:
            raise RuntimeError('Le répertoire d’état de la configuration conservée ne correspond pas.')
    backups=source.parent/'backups';backups.mkdir(mode=0o750,exist_ok=True)
    _write(backups/('config-fichier-'+stamp+'.json'),source.read_bytes(),0,0)
    _write(target,data,uid,gid)
    link=source.parent/('.config567-link-'+str(os.getpid()))
    try:
        link.symlink_to(target);os.replace(link,source)
    finally:
        link.unlink(missing_ok=True)
    return target
