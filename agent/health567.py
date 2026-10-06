"""Sondes locales : disponibilité de l'interface distincte de celle des producteurs."""
import json
import os
from pathlib import Path
import sqlite3
import time


def check(config_path, service=None, readiness=False):
    try:
        path=Path(config_path)
        cfg=json.loads(path.read_text(encoding='utf-8'))
        state=Path(cfg['state_dir'])
        if not state.is_dir() or not os.access(state,os.W_OK):
            return {'ok':False,'reason':'volume_non_accessible'}
        file=state/'desk.sqlite3'
        if not file.is_file():
            return {'ok':not (readiness or service),'reason':'base_non_initialisee'}
        db=sqlite3.connect('file:'+str(file)+'?mode=ro',uri=True,timeout=2)
        try:
            if db.execute('PRAGMA quick_check(1)').fetchone()[0]!='ok':
                return {'ok':False,'reason':'base_a_verifier'}
            if service:
                row=db.execute('SELECT heartbeat,status FROM live_services_v430 WHERE name=?',(service,)).fetchone()
                return {'ok':bool(row and time.time()-row[0]<90 and row[1] in ('active','paused','polling')), 'reason':'service_actif' if row and time.time()-row[0]<90 else 'heartbeat_absent_ou_ancien'}
            if readiness:
                if not cfg.get('installation',{}).get('done'):
                    return {'ok':False,'reason':'installation_a_terminer'}
                rows=db.execute("SELECT name,heartbeat FROM live_services_v430 WHERE name LIKE 'worker-%'").fetchall()
                if not any(time.time()-r[1]<90 for r in rows):
                    return {'ok':False,'reason':'aucun_worker_recent'}
            return {'ok':True,'reason':'interface_locale_disponible','remote_deliverables_verified':False}
        finally:db.close()
    except (OSError,ValueError,KeyError,sqlite3.Error):
        return {'ok':False,'reason':'configuration_ou_base_indisponible'}
