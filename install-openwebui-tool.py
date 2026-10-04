#!/usr/bin/env python3
"""Prepare the bounded AxiorHub tool for an administrator-controlled Open WebUI import."""
import hashlib
import json
import os
from pathlib import Path
import py_compile
import tempfile

from upgrade import verify, atomic


BASE=Path('/opt/axiorhub-mail-agent/current')
AUTH=Path('/etc/axiorhub-mail-agent/ui-auth.json')
TOKEN=Path('/etc/axiorhub-mail-agent/openwebui-api-token')
TARGET=Path('/etc/axiorhub-mail-agent/axiorhub_openwebui_tool.py')
PROMPT=Path('/etc/axiorhub-mail-agent/SYSTEM-PROMPT-AXIORHUB.md')


def main():
    if os.geteuid()!=0:raise RuntimeError('Exécuter avec sudo python3 install-openwebui-tool.py')
    verify(BASE.resolve())
    supported=('3.1.0','3.1.1','3.2.0','3.3.0','3.4.0','3.5.0','3.5.1',
               '3.6.0','3.6.1','3.6.2','3.6.3','3.6.4','3.6.5','3.7.0','3.8.0','3.8.1','3.9.0','3.9.1','3.9.2','3.9.3','4.0.0','4.1.0','4.1.1','4.2.0')
    if BASE.resolve().name not in supported:
        raise RuntimeError('Version active non prise en charge par l’installateur Open WebUI.')
    if not AUTH.is_file() or not TOKEN.is_file():
        raise RuntimeError('Exécuter d’abord sudo python3 install-interface.py pour créer le jeton API.')
    auth=json.loads(AUTH.read_text());token=TOKEN.read_text().strip()
    if hashlib.sha256(token.encode()).hexdigest()!=auth.get('api_token_sha256'):
        raise RuntimeError('Le jeton et son empreinte ne correspondent pas.')
    source=BASE/'integrations/openwebui/axiorhub_tool.py'
    with tempfile.TemporaryDirectory() as temporary:
        py_compile.compile(str(source),cfile=str(Path(temporary)/'tool.pyc'),doraise=True)
    atomic(TARGET,source.read_bytes(),0,0,0o600)
    atomic(PROMPT,(BASE/'integrations/openwebui/SYSTEM-PROMPT-AXIORHUB.md').read_bytes(),0,0,0o600)
    print('Outil Open WebUI préparé : '+str(TARGET))
    print('Dans Open WebUI : Espace de travail > Outils. Remplacer l’ancien outil AxiorHub par ce fichier ; ne pas créer de doublon.')
    print('Dans les Valves de l’outil, renseigner AXIORHUB_API_TOKEN avec :')
    print('  sudo cat '+str(TOKEN))
    print('URL OpenAPI alternative : '+auth['origin']+auth.get('prefix','/agent-courriel')+'/api/v1/openapi.json')
    print('Prompt système supervisé à copier dans votre modèle Open WebUI : '+str(PROMPT))
    print('Contrôle après import (le jeton reste lisible uniquement par root) : sudo /usr/bin/python3 '+str(BASE/'diagnostic-openwebui.py'))
    print('Aucune modification directe de la base Open WebUI n’a été effectuée.')


if __name__=='__main__':
    try:main()
    except RuntimeError as ex:raise SystemExit(str(ex)) from None
