#!/usr/bin/env python3
"""Secret-free validation of the AxiorHub tool imported into Open WebUI."""
import hashlib
import json
from pathlib import Path
from urllib.request import Request,urlopen
from urllib.error import HTTPError,URLError

AUTH=Path('/etc/axiorhub-mail-agent/ui-auth.json')
TOKEN=Path('/etc/axiorhub-mail-agent/openwebui-api-token')
TOOL=Path('/etc/axiorhub-mail-agent/axiorhub_openwebui_tool.py')

def main():
    report={'version':'4.2.0','token_exposed':False}
    try:
        auth=json.loads(AUTH.read_text());token=TOKEN.read_text().strip()
        report['token_matches_interface']=hashlib.sha256(token.encode()).hexdigest()==auth.get('api_token_sha256')
        source=TOOL.read_text();report['tool_present']=True
        report['tool_version_411']='version: 4.2.0' in source
        report['tool_has_hybrid_routing']='def afficher_le_routage_hybride(' in source
        report['tool_has_job_wait']='def _wait(' in source
        url=auth['origin']+auth.get('prefix','/agent-courriel')+'/api/v1/capabilities'
        request=Request(url,headers={'Authorization':'Bearer '+token,'Accept':'application/json'})
        with urlopen(request,timeout=30) as response:data=json.loads(response.read().decode())
        report['api_status']='ok';report['api_version']=data.get('version','')
        report['compatible']=(report['token_matches_interface'] and
          report['tool_version_411'] and report['tool_has_hybrid_routing'] and
          data.get('version')=='4.2.0')
    except FileNotFoundError as error:
        report.update(api_status='error',error='fichier_absent',detail=str(error),compatible=False)
    except PermissionError as error:
        report.update(api_status='error',error='permission_refusee',
          detail='Exécuter ce diagnostic avec sudo : le jeton Open WebUI est volontairement protégé en lecture root.',
          compatible=False)
    except (HTTPError,URLError,TimeoutError,ValueError) as error:
        report.update(api_status='error',error=type(error).__name__,compatible=False)
    print(json.dumps(report,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
