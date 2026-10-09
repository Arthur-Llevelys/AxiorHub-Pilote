"""TTS interchangeable, cache privé et budget réservé avant l'appel externe.

La conversation réutilise la dictée locale et les missions authentifiées.
Les clés n'atteignent jamais le navigateur ; aucun clonage de voix.
"""
from datetime import datetime,timezone
from decimal import Decimal
import fcntl
import hashlib
import io
import json
import os
from pathlib import Path
import re
import secrets
import tempfile
import wave

from .common import HTTP, Stop, read_secret
from . import assistant567, settings568


def _money(value):
    from .budget567 import money
    return money(value)


def preview(desk,text,owner='cabinet',matter=''):
    cfg=desk.c.get('speech568',{});provider=cfg.get('provider','espeak')
    external=provider=='elevenlabs';amount=None;spoken=text;redactions={};allowed=True;reason=''
    try:
        if not assistant567.profile(desk,owner)['speech_enabled']:raise Stop('lecture_vocale_desactivee')
        if external:
            spoken,redactions=_external_text(desk,text,owner,matter)
            price=_money(cfg.get('usd_per_1000_chars',0));cap=_money(cfg.get('per_request_usd',0));monthly=_money(cfg.get('monthly_usd',0))
            if min(price,cap,monthly)<=0:raise Stop('budget_voix_externe_non_configure')
            amount=price*Decimal(len(spoken))/Decimal(1000)
            if amount>cap:raise Stop('budget_voix_requete_depasse')
            used=sum((_money(r[0]) for r in desk.db.execute("SELECT amount FROM voice_usage_v568 WHERE month=? AND state IN ('reserved','charged','uncertain')",(datetime.now(timezone.utc).strftime('%Y-%m'),))),Decimal(0))
            if used+amount>monthly:raise Stop('budget_voix_mensuel_depasse')
    except Stop as exc:allowed=False;reason=str(exc)
    return {'provider':provider,'external':external,'characters':len(text),'estimated_usd':str(amount) if amount is not None else ('0' if not external else None),
            'allowed':allowed,'reason':reason,'transmitted_text':spoken if external and allowed else None,'redactions':redactions,'pseudonymized':external and spoken!=text,
            'policy':'La politique externe globale et les exclusions de dossiers restent applicables.',
            'voice_id':str(cfg.get('voice','')),'matter':matter}


def _external_text(desk,text,owner,matter):
    if not desk.c.get('speech568',{}).get('external_allowed'):raise Stop('voix_externe_non_autorisee')
    from .policy567 import check_external
    check_external({'provider_type':'openai','purpose':'assistant','external_data_allowed':True,'external_policy_config':desk.c},
                   {'matter':matter,'text':text})
    if matter in settings568.profile(desk,owner)['excluded_matters']:raise Stop('dossier_exclu_voix_externe')
    # La voix distante ne peut restaurer les identités dans un fichier audio.
    # Elle lit donc les pseudonymes. Le texte original reste lu par le local.
    from .model import pseudo_sources
    from .pseudo540 import Pseudonymizer
    ps=Pseudonymizer(pseudo_sources(desk.c));spoken=ps.apply(text)
    return spoken,ps.summary()


def _reserve(desk,owner,text):
    cfg=desk.c.get('speech568',{});price=_money(cfg.get('usd_per_1000_chars',0))
    cap=_money(cfg.get('per_request_usd',0));monthly=_money(cfg.get('monthly_usd',0))
    if min(price,cap,monthly)<=0:raise Stop('budget_voix_externe_non_configure')
    amount=price*Decimal(len(text))/Decimal(1000)
    if amount>cap:raise Stop('budget_voix_requete_depasse')
    month=datetime.now(timezone.utc).strftime('%Y-%m');ident=secrets.token_hex(16)
    desk.db.execute('BEGIN IMMEDIATE')
    try:
        used=sum((_money(r[0]) for r in desk.db.execute("SELECT amount FROM voice_usage_v568 WHERE month=? AND state IN ('reserved','charged','uncertain')",(month,))),Decimal(0))
        if used+amount>monthly:raise Stop('budget_voix_mensuel_depasse')
        desk.db.execute('INSERT INTO voice_usage_v568 VALUES(?,?,?,?,?,?)',(ident,owner,month,str(amount),'reserved',desk.now()));desk.db.commit()
        return ident
    except BaseException:desk.db.rollback();raise


def _repair_riff(raw):
    """5.6.16 : un WAV émis en flux (Kokoro-FastAPI avec stream, eSpeak) porte des tailles RIFF/data fictives (0 ou maximales) ;
    on les réécrit d'après la longueur réelle, sans toucher aux échantillons. Retourne raw inchangé si rien n'est réparable."""
    if not raw.startswith(b'RIFF') or raw[8:12]!=b'WAVE':return raw
    pos=12
    while pos+8<=len(raw):
        cid=raw[pos:pos+4];size=int.from_bytes(raw[pos+4:pos+8],'little')
        if cid==b'data':
            data_len=len(raw)-(pos+8)
            if data_len<=0:return raw
            return b'RIFF'+(len(raw)-8).to_bytes(4,'little')+raw[8:pos+4]+data_len.to_bytes(4,'little')+raw[pos+8:]
        pos+=8+size+(size&1)
    return raw


def _parse_wav(raw):
    with wave.open(io.BytesIO(raw),'rb') as w:
        if w.getnchannels() not in (1,2) or w.getsampwidth()!=2 or not 8000<=w.getframerate()<=96000 or w.getnframes()==0:raise ValueError()
        # Validate actual bytes, not only a RIFF header.
        if len(w.readframes(w.getnframes()))!=w.getnframes()*w.getnchannels()*2:raise ValueError()


def _wav(raw):
    if len(raw)>32_000_000 or not raw.startswith(b'RIFF'):raise Stop('audio_synthese_invalide')
    try:
        _parse_wav(raw);return raw
    except (wave.Error,EOFError,ValueError):
        repaired=_repair_riff(raw)
        if repaired is raw:raise Stop('audio_synthese_invalide') from None
    try:
        _parse_wav(repaired);return repaired
    except (wave.Error,EOFError,ValueError):raise Stop('audio_synthese_invalide') from None


def synthesize(desk,text,owner='cabinet',matter=''):
    text=str(text or '').strip();p=assistant567.profile(desk,owner)
    if not p['speech_enabled']:raise Stop('lecture_vocale_desactivee')
    if not 1<=len(text)<=7000:raise Stop('texte_lecture_trop_long_7000_maximum')
    cfg=desk.c.get('speech568',{});provider=cfg.get('provider','espeak')
    if provider not in ('espeak','kokoro','chatterbox','elevenlabs'):raise Stop('fournisseur_voix_invalide')
    spoken=text
    if provider=='elevenlabs':spoken,_=_external_text(desk,text,owner,matter)
    secret=cfg.get('api_key_file','')
    credential_stamp=str(Path(secret).stat().st_mtime_ns) if secret and Path(secret).is_file() else ''
    digest=hashlib.sha256(json.dumps([owner,matter,text,spoken,provider,cfg.get('url'),cfg.get('voice'),cfg.get('model'),p['speech_rate'],credential_stamp],ensure_ascii=False).encode()).hexdigest()
    root=Path(desk.c['state_dir'])/'voice568-cache';root.mkdir(mode=0o700,exist_ok=True);os.chmod(root,0o700)
    path=root/(digest+'.wav');lockpath=root/(digest+'.lock')
    with lockpath.open('a') as lock:
        os.chmod(lockpath,0o600);fcntl.flock(lock,fcntl.LOCK_EX)
        if path.is_file():return _cached_wav(path.read_bytes(),provider)
        if provider=='espeak':raw=assistant567.speech(desk,text,owner)
        elif provider in ('kokoro','chatterbox'):
            http=HTTP(cfg.get('url','http://127.0.0.1:8880'),local_only=True,local_hosts=('kokoro','chatterbox'),timeout=60)
            if secret:http.headers['Authorization']='Bearer '+read_secret(secret)
            body={'input':text,'model':cfg.get('model') or ('kokoro' if provider=='kokoro' else 'chatterbox'),
                  'voice':cfg.get('voice') or ('ff_siwis' if provider=='kokoro' else 'default'),
                  'response_format':'wav','speed':p['speech_rate'],'stream':False}   # 5.6.16 : fichier complet, pas de flux
            raw=http.request('POST',http.base+'/v1/audio/speech',json.dumps(body).encode(),{'Content-Type':'application/json'},32_000_000);raw=_wav(raw)
        else:
            voice=str(cfg.get('voice') or '')
            if not re.fullmatch(r'[A-Za-z0-9_-]{4,100}',voice):raise Stop('identifiant_voix_elevenlabs_requis')
            http=HTTP('https://api.elevenlabs.io',timeout=60)
            http.headers['xi-api-key']=read_secret(cfg.get('api_key_file',''))
            rid=_reserve(desk,owner,spoken)
            try:
                pcm=http.request('POST',http.base+'/v1/text-to-speech/'+voice+'?output_format=pcm_24000',
                    json.dumps({'text':spoken,'model_id':cfg.get('model') or 'eleven_multilingual_v2','language_code':'fr','voice_settings':{'speed':p['speech_rate']}}).encode(),{'Content-Type':'application/json'},32_000_000)
                if not pcm or len(pcm)%2:raise Stop('audio_synthese_invalide')
                output=io.BytesIO()
                with wave.open(output,'wb') as wav:wav.setnchannels(1);wav.setsampwidth(2);wav.setframerate(24000);wav.writeframes(pcm)
                raw=_wav(output.getvalue())
                desk.db.execute("UPDATE voice_usage_v568 SET state='charged' WHERE id=?",(rid,));desk.db.commit()
            except BaseException:
                desk.db.execute("UPDATE voice_usage_v568 SET state='uncertain' WHERE id=?",(rid,));desk.db.commit();raise
        fd,temp=tempfile.mkstemp(dir=root,prefix='.audio-')
        try:
            with os.fdopen(fd,'wb') as stream:stream.write(raw);stream.flush();os.fsync(stream.fileno())
            os.chmod(temp,0o600);os.replace(temp,path)
        finally:
            if Path(temp).exists():Path(temp).unlink()
        # Private, bounded retention. No content or secret is logged.
        import time
        files=sorted(root.glob('*.wav'),key=lambda x:x.stat().st_mtime,reverse=True)
        for stale in files:
            if stale!=path and (time.time()-stale.stat().st_mtime>86400 or files.index(stale)>=100):stale.unlink(missing_ok=True)
        desk.audit('synthese_vocale',{'owner':owner,'provider':provider,'characters':len(text),'external':provider=='elevenlabs'})
        return raw


def _cached_wav(raw,provider):
    # eSpeak's streamed WAV has an intentionally indeterminate RIFF length.
    if provider=='espeak':
        if not raw.startswith(b'RIFF') or len(raw)>32_000_000:raise Stop('audio_synthese_invalide')
        return raw
    return _wav(raw)


def speech(desk,text,owner='cabinet',matter=''):
    try:return synthesize(desk,text,owner,matter)
    except Stop as exc:
        if desk.c.get('speech568',{}).get('provider','espeak')=='espeak' or not desk.c.get('speech568',{}).get('fallback_local',True):raise
        desk.setting('voice568:last_fallback:'+owner,{'at':desk.now(),'reason':str(exc),'provider':'espeak'})
        return assistant567.speech(desk,text,owner)


def spoken_briefing_text(markdown):
    """5.6.19 : le briefing du matin (markdown) rendu lisible à voix haute : titres et puces en clair, heures en toutes lettres,
    sans balises ni liens. Chaque ligne se termine par une ponctuation pour que la synthèse marque la pause."""
    out=[]
    for raw in str(markdown or '').splitlines():
        line=raw.strip()
        if not line or line.startswith('*(Rubriques'):continue
        line=re.sub(r'^#+\s*','',line);line=re.sub(r'^[-*•]\s+','',line)
        line=line.replace('**','').replace('__','').replace('`','')
        line=re.sub(r'\[([^\]]+)\]\([^)]*\)',r'\1',line)
        line=re.sub(r'\b(\d{1,2})[:h](\d{2})\b',lambda m:str(int(m.group(1)))+' heures'+('' if m.group(2)=='00' else ' '+m.group(2)),line)
        line=line.replace(' – ',' à ').replace(' — ',' : ')
        if not line.endswith(('.',':','!','?')):line+='.'
        out.append(line)
    return '\n'.join(out)


def briefing(desk,owner='cabinet'):
    base=assistant567.briefing(desk,owner)
    profile=assistant567.profile(desk,owner)
    report=None
    if not profile.get('discreet'):
        try:
            from . import routines520
            report=routines520.latest(desk,'briefing')
        except Exception:
            report=None
    if report and str(report.get('text') or '').strip():
        base={**base,'text':'Briefing du matin.\n'+spoken_briefing_text(report['text'])[:6500],'source':'routine_briefing'}
    else:
        base={**base,'source':'instantane_anonymise' if profile.get('discreet') else 'instantane'}
    count=desk.db.execute("SELECT count(*) FROM commitments_v568 WHERE owner=? AND state NOT IN ('satisfied','cancelled')",(owner,)).fetchone()[0]
    news=desk.db.execute('SELECT title,published FROM news_v568 WHERE owner=? ORDER BY published DESC LIMIT 3',(owner,)).fetchall()
    from zoneinfo import ZoneInfo
    local=datetime.now(ZoneInfo(settings568.profile(desk,owner)['timezone']))
    extra=('\n'+str(count)+' engagement(s) suivis.' if count else '')
    if news:extra+='\nVeille publique : '+'. '.join(str(r['title'])[:180] for r in news)+'. Consultez les sources avant utilisation juridique.'
    return {**base,'text':str(base['text'])+extra,'timezone':settings568.profile(desk,owner)['timezone'],'local_date':local.date().isoformat(),'commitments':count,'news_count':len(news)}


def diagnostic(desk):
    cfg=desk.c.get('speech568',{});provider=cfg.get('provider','espeak')
    if provider in ('kokoro','chatterbox'):
        http=HTTP(cfg.get('url','http://127.0.0.1:8880'),local_only=True,local_hosts=('kokoro','chatterbox'),timeout=8)
        if cfg.get('api_key_file'):http.headers['Authorization']='Bearer '+read_secret(cfg['api_key_file'])
        value=http.json('GET','/v1/models')
        return {'ok':isinstance(value,dict),'provider':provider,'message':'API locale joignable. Tester ensuite la voix avec une phrase fictive.','remote_write':False}
    if provider=='elevenlabs':
        http=HTTP('https://api.elevenlabs.io',timeout=8);http.headers['xi-api-key']=read_secret(cfg.get('api_key_file',''))
        value=http.json('GET','/v1/voices')
        return {'ok':isinstance(value,dict),'provider':provider,'message':'Authentification ElevenLabs réussie ; aucun texte de dossier transmis.','remote_write':False}
    import shutil
    return {'ok':bool(shutil.which('espeak-ng')),'provider':provider,'message':'Présence du moteur eSpeak NG local vérifiée.'}
