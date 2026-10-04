#!/usr/bin/env python3
"""Read-only Docker inventory. No secret environment variables or audio."""
import json
import subprocess

def main():
    ids=subprocess.run(['docker','ps','-q'],check=True,capture_output=True,text=True).stdout.split()
    rows=json.loads(subprocess.run(['docker','inspect',*ids],check=True,capture_output=True,text=True).stdout) if ids else []
    output=[]
    allowed={'GRADIO_TEMP_DIR','WHISPER_DEVICE','DEVICE','CUDA_VISIBLE_DEVICES','WHISPER_IMPLEMENTATION'}
    for row in rows:
        ports=row.get('NetworkSettings',{}).get('Ports',{}) or {}
        image=row.get('Config',{}).get('Image','')
        if '7860' not in str(ports) and not any(x in image.lower() for x in ('whisper','vocal')):continue
        env={}
        for value in row.get('Config',{}).get('Env',[]):
            key,_,val=value.partition('=')
            if key in allowed:env[key]=val
        output.append({'container':row['Name'].lstrip('/'),'image':image,
          'image_id':row['Image'],'configured_user':row['Config'].get('User',''),
          'ports':ports,'safe_environment':env,
          'mounts':[{'type':m.get('Type'),'host':m.get('Source'),'container':m.get('Destination'),'writable':m.get('RW')} for m in row.get('Mounts',[])],
          'note':'Confirmer les chemins réels du cache et des sorties, leur UID/GID et le moteur CPU avant installation.'})
    print(json.dumps(output,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
