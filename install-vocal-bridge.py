#!/usr/bin/env python3
"""Explicit, separate installation. Does not touch existing Vocal or OpenAI keys."""
import argparse
from datetime import datetime,timezone
import grp
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
from upgrade import atomic,verify

BASE=Path('/opt/axiorhub-mail-agent/current')
DEST=Path('/opt/axiorhub-vocal-bridge-3.1.0')


def safe_directory(value):
    path=Path(value)
    if not path.is_absolute() or path.is_symlink() or not path.is_dir():raise RuntimeError('Répertoire Vocal absent ou ambigu.')
    resolved=path.resolve()
    if len(resolved.parts)<4 or resolved in (Path('/var/www/html'),Path('/var/lib/docker')):
        raise RuntimeError('Racine trop large : choisir précisément le cache Gradio ou le dossier outputs de Vocal.')
    return str(resolved)


def compose(cache,outputs,uid,gid):
    return {'services':{'axiorhub-vocal-bridge':{
      'build':'.','image':'axiorhub/vocal-bridge:3.1.0','container_name':'axiorhub-vocal-bridge',
      'restart':'unless-stopped','init':True,'user':f'{uid}:{gid}','read_only':True,
      'tmpfs':['/tmp:size=2g,mode=1777'],'ports':['127.0.0.1:9011:9011'],
      'extra_hosts':['host.docker.internal:host-gateway'],
      'environment':{'VOCAL_BASE_URL':'http://host.docker.internal:7860',
        'VOCAL_CPU_CONFIRMED':'true','BRIDGE_TOKEN_FILE':'/run/secrets/bridge-token',
        'GRADIO_ANALYTICS_ENABLED':'False','HF_HUB_DISABLE_TELEMETRY':'1',
        'MAX_AUDIO_BYTES':'536870912'},
      'volumes':['./bridge-token:/run/secrets/bridge-token:ro',cache+':/vocal-cache',outputs+':/vocal-output'],
      'networks':['axiorhub-audio'],'security_opt':['no-new-privileges:true'],'cap_drop':['ALL'],
      'logging':{'driver':'json-file','options':{'max-size':'10m','max-file':'3'}}}},
      'networks':{'axiorhub-audio':{'external':True}}}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--vocal-cache-dir',required=True,help='Chemin HÔTE du cache Gradio effectivement monté dans Vocal')
    p.add_argument('--vocal-container',required=True,help='Nom exact du conteneur Vocal existant')
    p.add_argument('--vocal-output-dir',required=True,help='Chemin HÔTE des sorties effectivement montées dans Vocal')
    p.add_argument('--vocal-cpu-confirmed',action='store_true',help='Vocal est réellement configuré en CPU ; int8 seul ne sélectionne pas le CPU')
    p.add_argument('--worker-uid',type=int,required=True,help='UID disposant de lecture/suppression dans ces deux dossiers')
    p.add_argument('--worker-gid',type=int,required=True)
    args=p.parse_args()
    if os.geteuid()!=0:raise RuntimeError('Exécuter avec sudo.')
    verify(BASE.resolve())
    if BASE.resolve().name not in ('3.1.0','3.1.1','3.2.0','3.3.0','3.4.0','3.5.0','3.5.1'):raise RuntimeError('Installer AxiorHub 3.1.0 d’abord.')
    if not args.vocal_cpu_confirmed or args.worker_uid<1 or args.worker_gid<1:raise RuntimeError('CPU et identité non-root du service à confirmer.')
    cache=safe_directory(args.vocal_cache_dir);outputs=safe_directory(args.vocal_output_dir)
    if cache==outputs or Path(cache) in Path(outputs).parents or Path(outputs) in Path(cache).parents:raise RuntimeError('Cache et sorties doivent être distincts.')
    if DEST.exists():raise RuntimeError('Installation passerelle déjà présente : conserver et contrôler manuellement ; aucun écrasement.')
    secret=Path('/etc/axiorhub-mail-agent/vocal-bridge-token')
    if secret.exists():raise RuntimeError('Secret AxiorHub préexistant : conservé. Vérifier la configuration avant activation.')
    group=grp.getgrnam('axiorhub-mail').gr_gid
    # Read-only Docker preflight: paths must be mounted in a running container
    # other than SpeakR. Administrator still confirms exact GRADIO_TEMP_DIR.
    ids=subprocess.run(['docker','ps','-q'],check=True,capture_output=True,text=True).stdout.split()
    containers=json.loads(subprocess.run(['docker','inspect',*ids],check=True,capture_output=True,text=True).stdout) if ids else []
    mounted=any(all(any(m.get('Source')==path and m.get('RW') for m in c.get('Mounts',[])) for path in (cache,outputs)) for c in containers if c.get('Name')=='/'+args.vocal_container and c.get('Name')!='/speakr')
    if not mounted:raise RuntimeError('Ces deux chemins ne sont pas montés en écriture dans un même conteneur Vocal actif. Ne pas utiliser des dossiers vides de remplacement.')
    os.umask(0o077);DEST.mkdir(mode=0o700)
    source=BASE/'integrations/vocal_bridge'
    for name in ('app.py','bridge.py','Dockerfile','requirements.txt'):shutil.copyfile(source/name,DEST/name)
    token=secrets.token_urlsafe(48)
    atomic(DEST/'bridge-token',(token+'\n').encode(),args.worker_uid,args.worker_gid,0o400)
    atomic(DEST/'compose.json',json.dumps(compose(cache,outputs,args.worker_uid,args.worker_gid),indent=2).encode(),0,0,0o600)
    check=subprocess.run(['docker','network','inspect','axiorhub-audio'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    if check.returncode:subprocess.run(['docker','network','create','axiorhub-audio'],check=True)
    cmd=['docker','compose','-f',str(DEST/'compose.json')]
    subprocess.run(cmd+['config','--quiet'],check=True)
    subprocess.run(cmd+['build'],check=True)
    subprocess.run(cmd+['up','-d'],check=True)
    # Separate activation is intentional: no confidential dictation until the
    # administrator has successfully tested Vocal and its remote cleanup.
    override={'services':{'app':{'environment':{
      'TRANSCRIPTION_BASE_URL':'http://axiorhub-vocal-bridge:9011/v1',
      'TRANSCRIPTION_MODEL':'large-v3-turbo','WHISPER_MODEL':'large-v3-turbo',
      'TRANSCRIPTION_CONNECTOR':'openai_whisper','TRANSCRIPTION_API_KEY':token},
      'networks':['default','axiorhub-audio']}},'networks':{'axiorhub-audio':{'external':True}}}
    atomic(DEST/'speakr-vocal.override.json',json.dumps(override,indent=2).encode(),0,0,0o600)
    atomic(secret,(token+'\n').encode(),0,group,0o640)
    print('Passerelle installée. SpeakR et le micro AxiorHub ne sont PAS encore basculés.')
    print('Effectuer le test audio non confidentiel et vérifier la suppression dans les deux répertoires Vocal.')
    print('Puis suivre GUIDE-3.1.0.md : override SpeakR et activation de la dictée.')


if __name__=='__main__':
    try:main()
    except (RuntimeError,subprocess.CalledProcessError) as error:
        raise SystemExit(str(error) if isinstance(error,RuntimeError) else 'Commande système interrompue. Aucune bascule automatique de SpeakR.') from None
