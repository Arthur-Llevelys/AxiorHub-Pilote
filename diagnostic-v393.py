#!/usr/bin/env python3
"""Live, secret-free AxiorHub 3.9.3 operational diagnostic.

Run as root so protected configuration metadata and the Open WebUI token can be
checked.  No secret or document content is printed and OpenRouter is not called.
"""
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parent
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))

from agent.common import load_config
from agent.desk import Desk
from agent.reliability393 import run_checks


def _public(value):
    if isinstance(value,dict):
        return {key:_public(item) for key,item in value.items()
                if key not in ('files','recent','stale','services')}
    if isinstance(value,list):return [_public(x) for x in value[:30]]
    return value


def main():
    config=Path('/etc/axiorhub-mail-agent/config.json')
    try:
        report=run_checks(Desk(load_config(config)),include_external=False)
        print(json.dumps({'version':'3.9.3','green_policy':'destination_read_back_required',
          'openrouter_tested':False,'checks':_public(report)},ensure_ascii=False,indent=2))
    except PermissionError:
        print(json.dumps({'version':'3.9.3','status':'error','error':'permission_refusee',
          'command':'sudo /usr/bin/python3 /opt/axiorhub-mail-agent/current/diagnostic-v393.py'},
          ensure_ascii=False,indent=2));raise SystemExit(2)


if __name__=='__main__':main()
