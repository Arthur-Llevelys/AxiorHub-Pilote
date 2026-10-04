#!/usr/bin/env python3
"""Explicit acceptance test using an administrator-chosen NON confidential clip."""
import argparse
import hashlib
import json
from pathlib import Path
import secrets
from urllib.request import Request,build_opener,ProxyHandler

def main():
    p=argparse.ArgumentParser();p.add_argument('audio',type=Path)
    p.add_argument('--non-confidential',action='store_true');args=p.parse_args()
    if not args.non_confidential:raise SystemExit('Utiliser un audio NON confidentiel et --non-confidential.')
    if not args.audio.is_file() or args.audio.stat().st_size>8_000_000:raise SystemExit('Fichier absent ou supérieur à 8 Mo.')
    ext=args.audio.suffix.lower()
    if ext not in {'.wav','.mp3','.m4a','.mp4','.ogg','.webm','.flac'}:raise SystemExit('Format non pris en charge.')
    raw=args.audio.read_bytes();boundary='axiorhub'+secrets.token_hex(20)
    body=(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="acceptance{ext}"\r\nContent-Type: application/octet-stream\r\n\r\n').encode()+raw+f'\r\n--{boundary}--\r\n'.encode()
    token=Path('/etc/axiorhub-mail-agent/vocal-bridge-token').read_text().strip()
    request=Request('http://127.0.0.1:9011/v1/audio/transcriptions',data=body,
      headers={'Authorization':'Bearer '+token,'Content-Type':'multipart/form-data; boundary='+boundary})
    try:
        with build_opener(ProxyHandler({})).open(request,timeout=1800) as response:
            result=json.loads(response.read(2_000_000))
        if not isinstance(result.get('text'),str) or not result['text'].strip():raise ValueError()
    except Exception:raise SystemExit('Test interrompu : vérifier la passerelle et Vocal. Aucun contenu ni secret affiché.') from None
    print(json.dumps({'status':'transcription_received','audio_sha256':hashlib.sha256(raw).hexdigest(),
      'characters':len(result['text']),'next':'Contrôler le nettoyage du cache ET des sorties Vocal avant activation.'}))

if __name__=='__main__':main()
