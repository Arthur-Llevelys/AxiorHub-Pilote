#!/usr/bin/env python3
"""Secret-free installation-layout diagnostic for AxiorHub 4.1.1."""
import json
import os
from pathlib import Path
import subprocess


BASE=Path('/opt/axiorhub-mail-agent')
CONFIG=Path('/etc/axiorhub-mail-agent/config.json')
STATE=Path('/var/lib/axiorhub-mail-agent')
UNITS=('axiorhub-mail-ui.service','axiorhub-mail-desk-worker.service',
       'axiorhub-mail-agent.timer','axiorhub-mail-agent-cleanup.timer')


def mode(path):
    try:return oct(path.stat().st_mode & 0o777)
    except OSError:return None


def active(unit):
    return subprocess.run(['systemctl','is-active','--quiet',unit],
      stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL).returncode==0


def main():
    current=BASE/'current';resolved=current.resolve() if current.is_symlink() else None
    releases=[]
    if (BASE/'releases').is_dir():
        releases=sorted(x.name for x in (BASE/'releases').iterdir() if x.is_dir())
    report={'version':'4.1.1','current':str(resolved) if resolved else '',
      'current_is_411':bool(resolved and resolved.name=='4.1.1'),
      'configuration_present':CONFIG.is_file(),'configuration_mode':mode(CONFIG),
      'state_present':STATE.is_dir(),'state_mode':mode(STATE),
      'release_count':len(releases),'releases':releases,
      'services':{unit:active(unit) for unit in UNITS},
      'secrets_exposed':False,
      'next_steps':[]}
    if not report['configuration_present']:
        report['next_steps'].append('sudo axiorhub-mail configure')
    if not all(report['services'].values()):
        report['next_steps'].append('Contrôler les services systemd après configuration.')
    report['status']='ok' if (report['current_is_411'] and report['configuration_present']
      and report['state_present']) else 'incomplete'
    print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
