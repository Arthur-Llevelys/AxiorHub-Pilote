#!/usr/bin/env python3
"""Enable browser dictation only after the explicit local acceptance test."""
import argparse
from datetime import datetime,timezone
import json
import os
from pathlib import Path
from upgrade import atomic
from agent.common import HTTP,read_secret

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--tested-audio-and-cleanup',action='store_true');args=parser.parse_args()
    if os.geteuid()!=0 or not args.tested_audio_and_cleanup:raise SystemExit('Sudo et --tested-audio-and-cleanup requis après le test audio non confidentiel.')
    if Path('/opt/axiorhub-mail-agent/current').resolve().name not in ('3.1.0','3.1.1','3.2.0'):raise SystemExit('Installer AxiorHub 3.1.0 d’abord.')
    path=Path('/etc/axiorhub-mail-agent/config.json');before=path.read_bytes();config=json.loads(before)
    secret='/etc/axiorhub-mail-agent/vocal-bridge-token';read_secret(secret)
    health=HTTP('http://127.0.0.1:9011',local_only=True,timeout=5).json('GET','/health')
    if health.get('status')!='ok':raise SystemExit('Passerelle indisponible.')
    backups=path.parent/'upgrade-backups';backups.mkdir(mode=0o700,exist_ok=True)
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    atomic(backups/('before-dictation-'+stamp+'.json'),before,0,0,0o600)
    config.setdefault('audio',{}).update(enabled=True,bridge_base_url='http://127.0.0.1:9011',bridge_token_file=secret,dictation_timeout_seconds=180,max_dictation_bytes=8000000)
    stat=path.stat();atomic(path,(json.dumps(config,indent=2,ensure_ascii=False)+'\n').encode(),stat.st_uid,stat.st_gid,stat.st_mode&0o777)
    print('Micro local activé. Recharger l’interface. Aucun fichier Nextcloud créé.')

if __name__=='__main__':main()
