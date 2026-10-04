#!/usr/bin/env python3
import os
from waitress import serve
from agent.web import App
from agent.standalone_auth import StandaloneAuth

data=os.environ.get('AXIORHUB_DATA_DIR','/data').rstrip('/')
config=os.environ.get('AXIORHUB_CONFIG',data+'/config.json')
auth=os.environ.get('AXIORHUB_INTERNAL_AUTH',data+'/internal-auth.json')
state=os.environ.get('AXIORHUB_AUTH_STATE',data+'/auth')
public=os.environ.get('AXIORHUB_PUBLIC_URL','https://agent.example.com')
application=StandaloneAuth(App(config,auth),state,public)

if __name__=='__main__':
    serve(application,host='0.0.0.0',port=int(os.environ.get('AXIORHUB_PORT','8626')),
          threads=max(24,int(os.environ.get('AXIORHUB_THREADS','24'))),max_request_body_size=25_000_000,
          trusted_proxy='*',trusted_proxy_count=1,
          trusted_proxy_headers={'x-forwarded-for','x-forwarded-proto','x-forwarded-host'},
          clear_untrusted_proxy_headers=True)
