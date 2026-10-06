"""Réglages de proactivité par utilisateur, sans secrets ni pouvoir d'envoi."""
from copy import deepcopy
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .common import Stop, load_matters

ROLES = {
 'A0': ('Coordonnateur', 'Préparer les étapes et éviter les doublons'),
 'A1': ('Courriels et engagements', 'Suivre les demandes et les promesses issues des envois réels'),
 'A2': ('Dossiers et pièces', 'Analyser les nouvelles pièces et leur provenance'),
 'A3': ('Contentieux et audiences', 'Préparer les écritures et les audiences'),
 'A4': ('Contrats et conseil', 'Analyser les clauses et préparer les projets'),
 'A5': ('Rendez-vous et réunions', 'Préparer le rendez-vous et un salon Talk privé'),
 'A6': ('Veille juridique', 'Collecter les nouveautés publiques sourcées'),
 'A7': ('Diligences et trésorerie', 'Préparer des propositions, jamais une facture définitive'),
 'A8': ('Contrôle qualité', 'Contrôler les sources sans lever un refus de sécurité'),
}
DEFAULTS = {'enabled': False, 'primary_address': '', 'aliases': [], 'timezone': 'Europe/Paris',
 'autonomy': 'prepare', 'roles': list(ROLES), 'excluded_matters': [], 'matter_modes': {},
 'lookback_days': 14, 'followup_days': 7, 'briefing_time': '08:00',
 'weekdays': [0,1,2,3,4], 'quiet_start': '20:00', 'quiet_end': '07:30',
 'max_suggestions': 3, 'daily_plan_limit': 12, 'vacation_until': '',
 'talk_enabled': False, 'meeting_days': 7, 'news_enabled': False,
 'legal_fields': [], 'news_sources': [], 'retention_days': 90}


def profile(desk, owner='cabinet'):
    result = deepcopy(DEFAULTS)
    result.update(desk.settings('proactive568:profile:'+owner, {}))
    return result


def owners(desk):
    rows = desk.db.execute("SELECT key FROM settings WHERE key LIKE 'proactive568:profile:%' ORDER BY key").fetchall()
    return [r['key'].split(':',2)[2] for r in rows]


def check_owner(desk,owner):
    p=profile(desk,owner)
    configured={str(x).lower() for x in desk.c['mail'].get('own_addresses',[])}
    if p['enabled'] and (set(p['aliases']+[p['primary_address']])-{''})-configured:raise Stop('identite_hors_compte_configure')
    if owner=='cabinet':return
    import os, sqlite3
    from pathlib import Path
    root=Path(os.environ.get('AXIORHUB_AUTH_STATE') or str(Path(os.environ.get('AXIORHUB_DATA_DIR','/data'))/'auth'))
    dbpath=root/'users.sqlite3'
    if not dbpath.is_file():raise Stop('utilisateur_proactif_non_verifiable')
    db=sqlite3.connect('file:'+str(dbpath)+'?mode=ro',uri=True)
    try:
        row=db.execute('SELECT active,role FROM users WHERE email=?',(owner,)).fetchone()
        if not row or not row[0] or row[1] not in ('administrateur','avocat'):raise Stop('utilisateur_proactif_non_autorise')
    finally:db.close()


def save(desk, data, owner='cabinet'):
    if not isinstance(data,dict) or set(data)-set(DEFAULTS):raise Stop('reglage_proactivite_inconnu')
    p = profile(desk,owner);p.update(data)
    for key in ('enabled','talk_enabled','news_enabled'):
        if not isinstance(p[key],bool):raise Stop('reglage_booleen_invalide')
    if p['autonomy'] not in ('observe','prepare','organize'):raise Stop('autonomie_proactivite_invalide')
    for k,lo,hi in (('lookback_days',1,90),('followup_days',1,60),('max_suggestions',1,10),
                    ('daily_plan_limit',1,50),('meeting_days',1,30),('retention_days',7,365)):
        if isinstance(p[k],bool) or not isinstance(p[k],int) or not lo<=p[k]<=hi:raise Stop('reglage_nombre_invalide')
    try:ZoneInfo(str(p['timezone']))
    except (ZoneInfoNotFoundError,ValueError,TypeError):raise Stop('fuseau_invalide') from None
    for k in ('briefing_time','quiet_start','quiet_end'):
        if not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d',str(p[k])):raise Stop('heure_invalide')
    if not isinstance(p['weekdays'],list) or not p['weekdays'] or any(type(v)!=int or not 0<=v<=6 for v in p['weekdays']):raise Stop('jours_invalide')
    p['weekdays']=sorted(set(p['weekdays']))
    if p['vacation_until']:
        from datetime import date
        try:date.fromisoformat(p['vacation_until'])
        except (ValueError,TypeError):raise Stop('date_vacances_invalide') from None
    for key,limit in (('aliases',20),('roles',9),('excluded_matters',500),('legal_fields',30),('news_sources',15)):
        if not isinstance(p[key],list) or len(p[key])>limit or any(not isinstance(v,str) or len(v)>600 for v in p[key]):raise Stop('reglage_liste_invalide')
        p[key]=list(dict.fromkeys(v.strip() for v in p[key] if v.strip()))
    if set(p['roles'])-set(ROLES):raise Stop('role_agent_invalide')
    p['primary_address']=str(p['primary_address']).strip().lower()
    p['aliases']=[x.lower() for x in p['aliases']]
    configured={str(x).lower() for x in desk.c['mail'].get('own_addresses',[])}
    identities=set(p['aliases']+[p['primary_address']])-{''}
    if any(not re.fullmatch(r'[^\s@<>]+@[^\s@<>]+',a) for a in identities) or identities-configured:
        raise Stop('identite_hors_compte_configure')
    if p['enabled'] and not p['primary_address']:raise Stop('adresse_utilisateur_requise')
    known={str(m['id']) for m in load_matters(desk.c)}
    if set(p['excluded_matters'])-known:raise Stop('dossier_exclu_inconnu')
    if not isinstance(p['matter_modes'],dict) or set(p['matter_modes'])-known or any(v not in ('observe','prepare','organize') for v in p['matter_modes'].values()):raise Stop('autonomie_dossier_invalide')
    from .news568 import validate_source
    for url in p['news_sources']:validate_source(url)
    # Deux profils ne déclenchent pas deux productions pour la même identité.
    desk.db.execute('BEGIN IMMEDIATE')
    try:
        if p['enabled']:
            for other in owners(desk):
                q=profile(desk,other)
                if other!=owner and q['enabled'] and identities.intersection(q['aliases']+[q['primary_address']]):raise Stop('identite_deja_observee')
        import json
        desk.db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',('proactive568:profile:'+owner,json.dumps(p)))
        desk.db.commit()
    except BaseException:desk.db.rollback();raise
    desk.audit('reglages_proactivite',{'owner':owner,'enabled':p['enabled'],'autonomy':p['autonomy']})
    return p


def mode(desk,owner,matter=''):
    p=profile(desk,owner)
    order={'observe':0,'prepare':1,'organize':2}
    choices=[p['autonomy'], p['matter_modes'].get(matter,p['autonomy']), desk.settings('proactive568:cabinet_mode','organize')]
    if not p['enabled'] or matter in p['excluded_matters'] or desk.settings('missions567:pause',False):return 'observe'
    from datetime import datetime
    if p['vacation_until'] and datetime.now(ZoneInfo(p['timezone'])).date().isoformat()<=p['vacation_until']:return 'observe'
    return min(choices,key=lambda x:order.get(x,0))
