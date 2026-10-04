#!/usr/bin/env python3
"""Read-only inventory. No mail bodies, credential values or full configs."""
import json
import os
from pathlib import Path
from localpaths import roundcube_root
import re
import shutil
import socket
import subprocess
import urllib.request

ROOT = roundcube_root()
SAFE_KEYS = {'imap_host', 'default_host', 'imap_port', 'default_port', 'drafts_mbox', 'sent_mbox',
             'ai_nextcloud_url', 'ai_nextcloud_username', 'ai_nextcloud_calendar_name',
             'ai_nextcloud_password_file', 'ai_nextcloud_allowed_roots', 'ai_ollama_url', 'ai_ollama_model'}


def literal_settings():
    result = {}
    paths = [ROOT/'config/config.inc.php', ROOT/'plugins/ai_roundcube_assistant/config.inc.php']
    for path in paths:
        if not path.is_file(): continue
        text = path.read_text(errors='replace')
        # Do not execute PHP, includes, scripts, or substitutions.
        for key in SAFE_KEYS:
            rx = r"\$config\[['\"]"+re.escape(key)+r"['\"]\]\s*=\s*(['\"])((?:\\.|(?!\1).)*)\1\s*;"
            match = re.search(rx, text)
            if match:
                value = match[2].replace("\\'", "'").replace('\\\\', '\\')
                if not re.search(r'\$|\r|\n', value): result[key] = value
        match = re.search(r"\$config\[['\"]ai_nextcloud_allowed_roots['\"]\]\s*=\s*\[([^\]]*)\]", text, re.S)
        if match:
            result['ai_nextcloud_allowed_roots'] = re.findall(r"'([^']+)'", match[1])
    return result


def run(args):
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=15)
        return p.stdout.strip() if p.returncode == 0 else 'indisponible'
    except (OSError, subprocess.TimeoutExpired): return 'indisponible'


def sanitized_settings(settings):
    out = {}
    for k,v in settings.items():
        if k.endswith('_password_file'):
            p = Path(v)
            out[k] = {'path': str(p), 'exists': p.is_file(), 'mode': oct(p.stat().st_mode & 0o777) if p.exists() else None}
        elif isinstance(v,str) and ('://' in v):
            from urllib.parse import urlsplit, urlunsplit
            p = urlsplit(v)
            out[k] = urlunsplit((p.scheme, p.hostname or '', p.path, '', ''))
        else: out[k] = v
    return out


def main():
    report = {'diagnostic_version': 1, 'python': run(['python3','--version']),
              'platform': run(['uname','-m']), 'roundcube_present': ROOT.is_dir(),
              'settings_non_secrets': sanitized_settings(literal_settings()),
              'tools': {n: bool(shutil.which(n)) for n in ['systemctl','pdftotext','pdfinfo','pdftoppm','tesseract']},
              'ocr_languages': run(['tesseract','--list-langs']) if shutil.which('tesseract') else 'absent',
              'services': {n: run(['systemctl','is-active',n]) for n in ['dovecot','ollama','apache2']},
              'containers': run(['docker','ps','--format','{{.Names}} | {{.Image}} | {{.Status}}']),
              'local_ports': {}}
    for port in [143,993,11434,5678,8090]:
        try:
            with socket.create_connection(('127.0.0.1',port),timeout=1): report['local_ports'][str(port)] = True
        except OSError: report['local_ports'][str(port)] = False
    try:
        op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with op.open('http://127.0.0.1:11434/api/tags',timeout=5) as r:
            data = json.loads(r.read(200000))
        report['ollama_models'] = [m.get('name') for m in data.get('models',[])]
    except Exception: report['ollama_models'] = 'indisponible'
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__': main()
