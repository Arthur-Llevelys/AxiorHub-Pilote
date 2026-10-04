#!/usr/bin/env python3
"""Configure the restricted Nextcloud account used for Calendar and Tasks."""
from datetime import datetime, timezone
import getpass
import grp
import json
import os
from pathlib import Path
import subprocess
import sys

from agent.common import Stop, private_json
from agent.dav import DAV


CONFIG=Path('/etc/axiorhub-mail-agent/config.json')
SECRET=Path('/etc/axiorhub-mail-agent/secrets/nextcloud-workflow.secret')


def choose(label,calendars,default_name='',multiple=False):
    print('\n'+label)
    for i,item in enumerate(calendars,1):
        print(str(i)+'. '+item['name']+' ['+', '.join(item.get('components',[]))+']')
    default=''
    if default_name:
        default=next((str(i) for i,x in enumerate(calendars,1) if x['name']==default_name),'')
    prompt='Numéro'+('s séparés par des virgules' if multiple else '')
    raw=input(prompt+(' ['+default+']' if default else '')+' : ').strip() or default
    try:
        indexes=[int(x.strip())-1 for x in raw.split(',') if x.strip()]
        selected=[calendars[i] for i in indexes]
    except (ValueError,IndexError):raise Stop('selection_calendrier_invalide') from None
    if not selected or (not multiple and len(selected)!=1):raise Stop('selection_calendrier_invalide')
    return selected if multiple else selected[0]


def main():
    if os.geteuid()!=0:raise Stop('configuration_necessite_sudo')
    if not CONFIG.is_file():raise Stop('configuration_axiorhub_absente')
    data=json.loads(CONFIG.read_text());old=CONFIG.read_bytes();stat=CONFIG.stat()
    current=data.get('nextcloud_workflow') or {}
    url=input('URL Nextcloud ['+current.get('url',data['nextcloud']['url'])+'] : ').strip() or current.get('url',data['nextcloud']['url'])
    default_user=current.get('username') or 'compte-technique@example.com'
    username=input('Login du compte technique ['+default_user+'] : ').strip() or default_user
    password=getpass.getpass('Mot de passe d’application Nextcloud (saisie masquée) : ')
    if not password or '\n' in password or '\r' in password:raise Stop('secret_vide_ou_invalide')
    SECRET.parent.mkdir(parents=True,exist_ok=True,mode=0o750)
    fd=os.open(SECRET,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o640)
    with os.fdopen(fd,'w') as stream:stream.write(password+'\n')
    gid=grp.getgrnam('axiorhub-mail').gr_gid;os.chown(SECRET,0,gid);os.chmod(SECRET,0o640)
    requested_roots=data['nextcloud'].get('matter_roots') or data['nextcloud']['roots']
    probe={'url':url,'username':username,'password_file':str(SECRET),
           'roots':requested_roots,'max_depth':8,'max_files':500,'max_file_bytes':15000000}
    dav=DAV(probe);calendars=dav.calendars()
    if not calendars:raise Stop('aucun_calendrier_partage_avec_compte_technique')
    event_calendars=[x for x in calendars if 'VEVENT' in x.get('components',[])]
    task_calendars=[x for x in calendars if 'VTODO' in x.get('components',[])]
    if not event_calendars:raise Stop('aucun_agenda_evenements_visible')
    if not task_calendars:raise Stop('aucune_liste_taches_vtodo_visible')
    readable=choose('Agendas à lire (audiences, rendez-vous et échéances)',event_calendars,'CABINET EXEMPLE',True)
    task=choose('Liste réservée aux tâches AxiorHub',task_calendars,'AxiorHub – Tâches proposées')
    planning=choose('Agenda réservé aux créneaux proposés',event_calendars,'AxiorHub – Planning proposé')
    others=[x for x in task_calendars if x['url']!=task['url']]
    extra_tasks=[]
    if others:
        print(chr(10)+'Autres listes de tâches visibles (affichées en lecture seule dans « Agenda et tâches »)')
        for i,item in enumerate(others,1):print(str(i)+'. '+item['name'])
        raw=input('Numéros séparés par des virgules (Entrée = aucune) : ').strip()
        try:extra_tasks=[others[int(x.strip())-1]['url'] for x in raw.split(',') if x.strip()]
        except (ValueError,IndexError):raise Stop('selection_calendrier_invalide') from None
    if task['url']==planning['url']:raise Stop('taches_et_planning_ne_doivent_pas_partager_le_meme_calendrier')
    readable_roots=[]
    for root in requested_roots:
        try:dav.list_folder(root);readable_roots.append(root)
        except Stop:pass
    if not readable_roots:raise Stop('aucun_dossier_client_partage_avec_compte_technique')
    probe['roots']=readable_roots
    workflow={**probe,'enabled':True,'calendar_read_urls':[x['url'] for x in readable],
      'task_calendar_url':task['url'],'task_read_urls':extra_tasks,'planning_calendar_url':planning['url'],
      'project_folder':'90 - Projets IA',
      'notes_enabled':False,'deck_enabled':False}
    data['nextcloud_workflow']=workflow
    automation=data.setdefault('automation',{})
    automation['nextcloud_tasks_enabled']=True
    automation.setdefault('nextcloud_tasks_interval_minutes',15)
    backup=CONFIG.parent/'upgrade-backups'/('config-before-nextcloud-workflow-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'.json')
    backup.parent.mkdir(parents=True,exist_ok=True,mode=0o700);backup.write_bytes(old);os.chmod(backup,0o600)
    private_json(CONFIG,data);os.chown(CONFIG,stat.st_uid,stat.st_gid);os.chmod(CONFIG,stat.st_mode & 0o777)
    for unit in ('axiorhub-mail-ui.service','axiorhub-mail-desk-worker.service'):
        subprocess.run(['systemctl','restart',unit],check=True)
    print('\nConfiguration enregistrée.')
    print('Agendas lus : '+', '.join(x['name'] for x in readable))
    print('Dossiers clients lisibles : '+', '.join(readable_roots))
    print('Tâches écrites uniquement dans : '+task['name'])
    print('Créneaux écrits uniquement dans : '+planning['name'])
    print('Notes et Deck restent désactivés dans la version 2.4.0.')
    print('Aucun droit de suppression, partage public, envoi, paiement, dépôt ou signature n’est exposé.')


if __name__=='__main__':
    try:main()
    except (Stop,subprocess.CalledProcessError) as ex:sys.exit('ARRÊT : '+str(ex))
