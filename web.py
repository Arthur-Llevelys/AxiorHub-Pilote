#!/usr/bin/env python3
import argparse
import os
from agent.common import load_config

if __name__=='__main__':
    os.umask(0o077)
    p=argparse.ArgumentParser()
    p.add_argument('mode',choices=['serve','worker','watch'])
    p.add_argument('--worker-id',default='1')
    p.add_argument('--config',default='/etc/axiorhub-mail-agent/config.json')
    p.add_argument('--auth',default='/etc/axiorhub-mail-agent/ui-auth.json')
    args=p.parse_args()
    if args.mode=='serve':
        from agent.web import serve
        serve(args.config,args.auth)
    elif args.mode=='worker':
        from agent.desk import worker
        worker(load_config(args.config),args.worker_id)
    else:
        from agent.watch430 import watcher
        watcher(load_config(args.config))
