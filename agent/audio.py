"""Read-only health check for the local Vocal bridge."""
import json
import secrets
from .common import HTTP, Stop, read_secret


def dictate(config,raw,content_type):
    audio=config.get('audio',{})
    if not audio.get('enabled'):raise Stop('dictee_locale_non_configuree')
    if content_type not in ('audio/webm','audio/ogg','audio/mp4','audio/wav'):raise Stop('format_audio_refuse')
    if not raw or len(raw)>int(audio.get('max_dictation_bytes',8000000)):raise Stop('dictee_trop_volumineuse')
    extension={'audio/webm':'webm','audio/ogg':'ogg','audio/mp4':'m4a','audio/wav':'wav'}[content_type]
    boundary='axiorhub'+secrets.token_hex(20)
    body=(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="dictation.{extension}"\r\nContent-Type: {content_type}\r\n\r\n').encode()+raw+f'\r\n--{boundary}--\r\n'.encode()
    client=HTTP(audio.get('bridge_base_url','http://127.0.0.1:9011'),local_only=True,local_hosts=audio.get('local_hosts',()),
                timeout=min(240,int(audio.get('dictation_timeout_seconds',180))))
    result=client.request('POST',client.base+'/v1/audio/transcriptions',body,
      {'Authorization':'Bearer '+read_secret(audio['bridge_token_file']),
       'Content-Type':'multipart/form-data; boundary='+boundary},200000)
    try:text=json.loads(result)['text']
    except (ValueError,KeyError,TypeError):raise Stop('transcription_invalide') from None
    if not isinstance(text,str) or len(text)>30000:raise Stop('transcription_invalide')
    return {'text':text,'submitted':False}


def bridge_status(config):
    audio=config.get('audio',{})
    configured=bool(audio.get('enabled',False))
    result={'configured':configured,'reachable':False,
            'base_url':audio.get('bridge_base_url','http://127.0.0.1:9011')}
    if not configured:
        result['status']='disabled'
        return result
    try:
        health=HTTP(result['base_url'],local_only=True,
                    timeout=int(audio.get('health_timeout_seconds',5))).json('GET','/health')
        result.update({'reachable':health.get('status')=='ok','status':health.get('status','unknown'),
                       'model':health.get('model'),'language':health.get('language'),
                       'compute_type':health.get('compute_type'),'device':health.get('device')})
    except Stop:
        result['status']='unreachable'
    return result
