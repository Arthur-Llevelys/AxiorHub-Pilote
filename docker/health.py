#!/usr/bin/env python3
import os
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from agent.health567 import check
result=check(os.environ.get('AXIORHUB_CONFIG','/data/config.json'),service=sys.argv[1] if len(sys.argv)>1 else None)
print(result['reason'])
raise SystemExit(0 if result['ok'] else 1)
