#!/usr/bin/env python3
import argparse
import os
from agent.common import load_config

if __name__=='__main__':
    os.umask(0o077)
    p=argparse.ArgumentParser()
    p.add_argument('mode',choices=['serve','worker','watch','liaison'])   # 5.6.103 (lot L) : liaison entre postes (poste.py, liaison activée)
    p.add_argument('--worker-id',default='1')
    p.add_argument('--config',default='/etc/axiorhub-mail-agent/config.json')
    p.add_argument('--auth',default='/etc/axiorhub-mail-agent/ui-auth.json')
    p.add_argument('--port',type=int,default=8769)   # 5.6.20 : mode poste
    args=p.parse_args()
    try:
        if args.mode=='serve':
            from agent.web import serve
            serve(args.config,args.auth,args.port)
        elif args.mode=='worker':
            from agent.desk import worker
            worker(load_config(args.config),args.worker_id)
        elif args.mode=='liaison':   # 5.6.103 (lot L) : lancé par poste.py seulement quand la liaison est activée ; s'arrête quand elle ne l'est plus
            from agent.liaison56103 import service
            service(load_config(args.config))
        else:
            from agent.watch430 import watcher
            watcher(load_config(args.config))
    except Exception as error:   # 5.6.109 (lot R) : base illisible : message unique, délai croissant, code 78 (pas de boucle de redémarrage serrée)
        from agent.secours56109 import arret_service
        arret_service(args.config,args.mode if args.mode!='worker' else 'worker-'+str(args.worker_id),error)
        raise
